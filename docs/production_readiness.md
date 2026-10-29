# Production readiness: how this would ship at a large company

This repo is a single-node reference implementation. The sections below cover what changes when
thousands of analysts and PMs use it against a warehouse with 10^5 tables. Where a decision rests on
something measured in this repo, the number is cited with the file that produces it.

## 1. Identity, access control and row-level security
- **Run every query as the end user, not as a service account.** The API takes the caller's identity
  (OIDC/OAuth, on-behalf-of token) and opens the warehouse session with it. Snowflake, BigQuery,
  Databricks Unity Catalog and Trino/Ranger then apply that person's grants, **row access policies**
  (for example a regional ops lead sees only their region) and **dynamic masking policies**. The
  agent cannot widen access because it never holds more privilege than the user.
  - LinkedIn's SQL Bot does the same: if the user lacks a dataset permission but belongs to a group
    that has it, the bot generates code that uses the group credential. It never uses its own.
- **Keep the agent-side guardrails anyway.** These are the AST validator, the read-only session, the
  table allowlist and the row/time caps (`docs/security.md`). They are cheap, they fail closed, and
  they protect against misconfigured grants.
  - Red-teaming found five holes here: two at the connection layer (ATTACH creating files, and raw
    PII readable next to masking views), invertible zip tokens, an allowlist bypass through CTE
    shadowing, and an unbounded-memory query. All five are fixed and have regression tests
    (`docs/security.md`).
- **PII.** Use warehouse-native classification plus masking policies. Tokenize join keys with a
  *keyed* function (HMAC with a key held by the platform), so joins still work but tokens cannot be
  inverted. An unkeyed hash of a low-cardinality value is a lookup table, not masking: this repo's
  v1 used `md5(zip)`, and a single SELECT re-identified all 99,441 customers (`docs/security.md`).
  Free-text columns get PII detection before they are exposed. Result rows are logged only after
  masking.
- **Audit.** Log every (user, question, SQL, tables touched, rows returned, cost) to an append-only
  audit table. The JSONL traces here are the prototype of that record.

## 2. Data catalog integration (schema retrieval at real scale)
- The catalog here (`catalog.py`) is built from the database plus hand-written docs. In production,
  index the company catalog (DataHub, Amundsen, Unity Catalog, Dataplex). Include table and column
  descriptions, owners, **certification status**, popularity from query logs, common join paths, and
  deprecation flags.
  - LinkedIn automatically drops deprecated tables from the index and adds popular ones.
  - Pinterest found that weighting table documentation in the embeddings moved table-search hit rate
    from 40% to 90%.
- **Narrow the search space before retrieving.** Uber's QueryGPT routes each question to a business
  "workspace" (an intent agent) before running RAG over tables and SQL samples. LinkedIn narrows by
  the user's org and access history, then has an LLM re-rank the top-20 tables down to 7.
- **What this repo measured.**
  - Inside one BIRD database, BM25 plus FK expansion reaches strict all-gold-tables recall of 0.986
    at k=6. These databases have only 3–13 tables, so retrieval barely prunes there.
  - At "warehouse scale", with all 11 BIRD databases plus Olist pooled into one index of 84 tables,
    strict recall@10 falls to 0.904 (BM25) and 0.946 (hybrid BM25 + bge-small with RRF), and top-1
    database routing is 0.97–0.99.
  - That gap is where production effort goes: re-ranking, workspaces, and certified-table boosts.
  - Source: `results/retrieval_recall.json`.

## 3. Governed semantic layer first, raw SQL second
- Business metrics (GMV, AOV, active customers, on-time rate) are defined once, in a governed layer:
  dbt Semantic Layer / MetricFlow, Airbnb's Minerva, Looker's LookML. The agent's first move is to
  *select* metrics, dimensions and filters, and a deterministic compiler writes the SQL. Raw SQL is a
  reviewed fallback.
- The controlled experiment in `docs/semantic_layer_experiment.md` measures what this buys: accuracy,
  definition drift, and answers that disagree with each other across paraphrases. Its numbers are the
  argument for funding the semantic layer before scaling the bot.

## 4. Human in the loop
- **Always show the SQL and the result table** next to the natural-language summary. The summary is
  display-only and is never executed.
- **Calibrated confidence and abstention.**
  - Low-confidence answers get a "review before sharing" badge and an option to route the question to
    the owning analytics team's queue.
  - Ambiguous questions get a clarifying question instead of a guess.
  - Measured coverage/accuracy trade-offs are in `results/abstention.json` and `results/ambiguity__haiku.json`.
- **Guided mode** for high-stakes requests: the user confirms the tables or metrics before
  execution. LinkedIn and QueryGPT both added table-confirmation steps after users asked for control.
- **Certified answers.** Once an analyst approves a (question, SQL) pair, it is promoted into the
  verified example store. The same store feeds few-shot retrieval (`retrieval.ExampleStore`), so the
  system improves where it is used most.

## 5. Online feedback and labels
- Capture thumbs up/down, "edit SQL", "fix with AI" and "copied to dashboard" events, keyed by trace
  id. Every event already maps to a trace with the SQL, the tables touched and the cost.
- An analyst's edit to generated SQL is the most valuable label there is: it is a corrected
  (question, SQL) pair. Sample those edits every week for the golden set, the same way Uber built its
  golden set from real logged questions.
- **Online metrics to track:**
  - acceptance rate. Pinterest reported first-shot acceptance rising from 20% to above 40%.
  - edit distance to the accepted SQL
  - abstention rate and clarification rate
  - share of answers served through governed metrics
  - $ per answered question, and p95 latency

## 6. Regression evals in CI and in production
- **Pre-merge gate.** This repo ships one in `tests/test_regression_gate.py`: a frozen set of 30 questions, recorded
  LLM responses, and an accuracy threshold.
  - A prompt change invalidates the recording. The author must re-record with the real model, and
    the new run must clear the threshold.
  - A recorded "token-saving" prompt change (column docs and hints removed) is checked in as a
    negative control. It must fail the gate.
- **Nightly.** Run the full benchmark plus the business golden set, with bootstrap CIs. Alert on drops
  that exceed run-to-run noise. Uber reports ignoring changes of about 5% because of LLM
  non-determinism; measure your own noise floor with repeated runs and do not guess it.
- **Shadow mode.** Run new prompts or models on live traffic without serving their answers, and compare
  their result-equivalence with production.

## 7. Cost and latency controls
- **Route by confidence.** Use the cheap model first and escalate only on low confidence. The routing
  measurement is in `results/routing.json` and the cost/quality frontier is in
  `docs/img/frontier_cost.png`.
- **Cache.**
  - Response cache keyed by (prompt, schema version). This repo's on-disk cache made every re-run of
    the evals free.
  - Prompt caching for the long schema prefix.
  - Result cache for identical SQL on unchanged partitions.
- **Budgets.**
  - Per-user and per-team daily $ caps, enforced in an LLM gateway.
  - A hard call budget, as in `llm/ledger.py`.
  - Max tokens per step.
  - A kill switch.
- **Before execution.** Dry-run the query to estimate bytes scanned (BigQuery) or read the EXPLAIN cost
  (Snowflake/Trino). Refuse or confirm above a threshold.
- **Self-hosted model.** A local open model (qwen2.5:7b here) gives data residency at a large
  accuracy cost (see the frontier).

## 8. Deployment shape
Stateless FastAPI service (this repo's `serving/api.py`) behind the company API gateway. LLM calls go
through an internal LLM gateway (auth, quotas, logging, model fallback). Traces go to OpenTelemetry;
the span schema here was designed to map onto OTel. The vector or BM25 index is rebuilt from the
catalog on every catalog change. An MCP server exposes `ask_warehouse` and `list_tables` to other
internal agents, with the same identity propagation.

## 9. The gap between benchmark and real usage
BIRD numbers say little about production on their own. The table below explains why.

| BIRD Mini-Dev | Real warehouse |
|---|---|
| 3–13 tables per DB, schema given | 10^4–10^5 tables; finding the table is half the job |
| Clean questions, often with an "evidence" hint that names the formula | Vague, under-specified questions in jargon; no hints |
| One gold SQL, scored by exact result-set equality | ~60% of LinkedIn's benchmark questions had multiple valid answers; correct-but-different SQL is common |
| Static DB | Late-arriving data, stale or deprecated tables, schema changes |
| No access control | Entitlements decide what may be answered at all |
| Labels are assumed correct | Labels are noisy: our error analysis attributes part of the measured "failures" to gold or benchmark issues (`docs/error_analysis.md`) |

How to close the gap:
- Build the golden set from logged questions, and use the decoupled evaluation that Uber describes
  (score intent, table selection and SQL separately).
- Calibrate an LLM judge against humans, as LinkedIn did: its judge was within 1 point of the human
  score 75% of the time.
- Track online acceptance and edit rates.
- Re-label benchmark failures before trusting a delta of a few points.
