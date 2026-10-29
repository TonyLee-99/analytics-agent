# Acceptance criteria

Written 2026-10-29, after downloading the data and writing the compiler, catalog, guardrail and
executor skeletons, and **before any LLM evaluation was run**. The targets were not changed after
results came in. Each criterion is measurable and names the command that checks it. The PASS/FAIL
column is filled in at the end (see "Results").

## A. Data integrity
| ID | Criterion | Verify |
|----|-----------|--------|
| A1 | BIRD Mini-Dev (2026 "Mini-Dev 1024" release, SQLite, 500 questions): every gold SQL runs on the downloaded databases with 0 errors and 0 empty results | `make verify-data` |
| A2 | Olist row counts match the published dataset exactly (orders 99,441, order_items 112,650, ...) | `make olist` (fails on mismatch) |
| A3 | Olist golden set has 40–60 questions; every gold query and every definition variant runs; each variant gives a different answer from gold | `uv run python scripts/verify_olist_sets.py` |

## B. Schema retrieval
| ID | Criterion | Verify |
|----|-----------|--------|
| B1 | On BIRD, strict table recall (all gold tables retrieved) is ≥ 0.95 at the k the agent uses | `make retrieval` → `results/retrieval_recall.json` |
| B2 | On Olist, strict table recall is ≥ 0.95 at the k the agent uses | same |
| B3 | Warehouse-scale recall (all databases pooled into one index) is reported for BM25, dense and hybrid | same |

## C. Accuracy on BIRD (Haiku unless stated)
| ID | Criterion | Verify |
|----|-----------|--------|
| C1 | Reproduction check: with BIRD's published fixed prompt, our Haiku 5.5 EX on SQLite is within ±5 pp of the published 59.60 | `make eval-full` → `results/runs/bird__a_zeroshot_official__haiku.jsonl` |
| C2 | The best agent configuration beats the zero-shot baseline (a); paired McNemar p < 0.05 | `make report` → `results/ablations.md` |
| C3 | Self-correction (d) improves EX over the same pipeline without it (c) | same |
| C4 | Every ablation step (a→a2→b→c→d→e) is reported, including any that hurt | same |
| C5 | Sonnet and the local qwen2.5:7b are compared on the same frozen 200-question subset | same |

## D. Knowing when not to answer
| ID | Criterion | Verify |
|----|-----------|--------|
| D1 | Selective prediction on BIRD sub200: EX at 80 % coverage ≥ EX at 100 % coverage + 5 pp | `make abstention` → `results/abstention.json`, `docs/img/risk_coverage_*.png` |
| D2 | The confidence score separates right from wrong answers: AUROC ≥ 0.70 on BIRD sub200 | same |
| D3 | Same curve reported on the Olist golden set | same |
| D4 | Ambiguity: the agent asks a clarifying question on ≥ 70 % of the curated ambiguous questions and on ≤ 15 % of the clear controls | `make ambiguity` → `results/ambiguity.json` |

## E. Governed semantic layer vs raw SQL (Olist)
| ID | Criterion | Verify |
|----|-----------|--------|
| E1 | Semantic-layer arm EX on metric questions is higher than the raw-SQL arm's | `make semantic` → `results/semantic_experiment.json` |
| E2 | Metric-definition inconsistency rate (an answer matching a non-governed variant definition, or paraphrases disagreeing) is reported per arm; semantic arm ≤ half the raw arm's | same |
| E3 | Olist golden set EX ≥ 0.80 for the best arm (Haiku) | same |

## F. Cost–quality frontier and routing
| ID | Criterion | Verify |
|----|-----------|--------|
| F1 | EX vs $/question and EX vs p95 latency are plotted for ≥ 5 configurations (qwen, haiku variants, sonnet, self-consistency, routed) | `make frontier` → `docs/img/frontier_*.png` |
| F2 | Routed system (Haiku first, escalate to Sonnet on low confidence), measured by actually running the router on sub200: EX ≥ Sonnet-only EX − 2 pp, at ≤ 60 % of Sonnet-only cost per question. Pre-registered threshold (fixed before any self-consistency result existed): escalate when fewer than 4 of 5 Haiku samples agree (agreement < 0.8) | `results/routing.json` |

## G. Safety
| ID | Criterion | Verify |
|----|-----------|--------|
| G1 | 100 % of DDL/DML/file/extension attempts in the red-team SQL suite (≥ 40 cases) are blocked | `make redteam` → `results/redteam.json` |
| G2 | 0 false positives: all 500 BIRD gold queries and all Olist gold queries pass the validator | same |
| G3 | Read-only connection rejects writes even when the validator is bypassed | `pytest tests/test_executor.py` |
| G4 | No raw (un-tokenized) zip code ever appears in an agent result in the red-team run | `make redteam` |
| G5 | Prompt-injection suite (malicious text in questions and in data values): 0 unsafe statements executed | same |
| G6 | Timeout and row limit are enforced | `pytest tests/test_executor.py` |

## H. Engineering and operations
| ID | Criterion | Verify |
|----|-----------|--------|
| H1 | Per-question latency p50/p95, tokens and $ cost are reported for every run | `results/ablations.md` |
| H2 | Every agent step is traced to JSONL (spans with timing, tokens, cost); a trace summary report exists | `make traces` → `results/trace_summary.md` |
| H3 | FastAPI `/ask`, the chat UI and the MCP server (`ask_warehouse`, `list_tables`) each pass a smoke test | `pytest tests/test_serving.py`, `scripts/smoke_mcp.py` |
| H4 | Regression eval gate: a frozen set with recorded LLM responses and a pass threshold runs in CI without network; a deliberately degraded prompt is shown to fail it | `pytest tests/test_regression_gate.py` |
| H5 | pytest passes, `ruff check` is clean, Dockerfile + CI yaml exist, deps are pinned with uv on Python 3.12, `make eval-small` / `make eval-full` exist | `make test lint` |
| H6 | Total Claude CLI calls ≤ 4,000, with total $ reported from the CLI's own `total_cost_usd` | `uv run python -m analytics_agent.llm.ledger` |

## I. Error analysis
| ID | Criterion | Verify |
|----|-----------|--------|
| I1 | ≥ 60 BIRD failures hand-categorized into a taxonomy, with the share caused by gold-label or benchmark errors quantified | `docs/error_analysis.md`, `eval_sets/bird_failure_labels.jsonl` |
| I2 | For each pipeline change, show which error buckets it moved | same |

## J. Resources
| ID | Criterion | Verify |
|----|-----------|--------|
| J1 | Project data + intermediates ≤ 3.5 GB | `du -sh data .llm_cache` |

## Results

Filled in on 2026-10-29, after all runs. **35 criteria: 31 PASS, 1 PASS with a caveat (C3), 3 FAIL (D2, D4, F2).** Every row names the
command and quotes its output.

| ID | Result | Evidence |
|----|--------|----------|
| A1 | PASS | `make verify-data` → `BIRD gold SQL: 500 queries, 0 errors, 0 empty` |
| A2 | PASS | `make olist` → `orders rows= 99,441 expected= 99,441 OK` (all 9 tables OK, SHA-256 printed) |
| A3 | PASS | `verify_olist_sets.py` → 50 gold, 65 variants, 52 interpretations, 20 controls; `all SQL executed OK` |
| B1 | PASS | `results/retrieval_recall.json` `bird_within_db.bm25+fk.recall@6` = **0.986** (k=6 is what the agent uses). Caveat: that is 5.8 of 6.7 tables on average, so there is little pruning |
| B2 | PASS | `olist_within_db.bm25+fk.recall@6` = **1.0** |
| B3 | PASS | warehouse scale (84 tables): recall@10 BM25 0.904 / dense 0.909 / hybrid 0.946; top-1 DB routing 0.969 / 0.978 / 0.993 |
| C1 | PASS | `bird__a_zeroshot_official__haiku`: EX **62.6%** vs published 59.60% (+3.0 pp, within ±5) |
| C2 | PASS | (d) 73.2% vs (a) 62.6%, McNemar +107/−54, **p = 4e-5** (`results/ablations.json`) |
| C3 | PASS (caveat) | (c) 71.6% → (d) 73.2% (+15/−7, p = 0.13, *not significant*). Within run (d), correction fired on 14 questions and fixed 9: an exact +1.8 pp (`docs/error_analysis.md`) |
| C4 | PASS | every step reported in `results/ablations.md`, including the ones that hurt: retrieval vs full schema, 76.5 → 72.0 on sub200 |
| C5 | PASS | sub200: Sonnet (d) 86.0%, Haiku (d) 74.5%, qwen2.5:7b (d) 13.5% (all n=200, paired tests in `results/ablations.md`) |
| D1 | PASS | `results/abstention.json` bird_sub200 agreement: EX@80% = **82.5** vs EX@100% = 76.5 (+6.0 pp ≥ +5) |
| D2 | **FAIL** | AUROC = **0.692** (target ≥ 0.70); the logistic-regression variant is 0.653 |
| D3 | PASS | Olist raw arm (SC ×5): EX@100 62.0, EX@80 65.0, AUROC 0.61. Reported; weak, because definitional errors are self-consistent (mean agreement 0.75 vs 0.81 for correct answers) |
| D4 | **FAIL** | `results/ambiguity__haiku.json`: clarify rate on ambiguous questions **60%** for the raw arm (target ≥ 70%) and 35% for the semantic arm; false-clarify rate on controls **0%** for both (target ≤ 15% met) |
| E1 | PASS | `results/semantic_experiment__haiku.json`: metric-question EX semantic **96.7%** vs raw 46.7% |
| E2 | PASS | definition drift semantic **0%** vs raw **26.7%** (≤ half); paraphrase consistency 4/4 vs 2/4 |
| E3 | PASS | best arm EX: hybrid 94.0% (post-hoc design); best pre-planned arm, glossary, 90.0% (≥ 80%) |
| F1 | PASS | `docs/img/frontier_cost.png`, `frontier_latency.png`: 11 configurations (2 qwen, 6 Haiku, Sonnet, 2 routed) |
| F2 | **FAIL** | the router was actually run (`results/routing.json`): EX **81.5%**, below the target of ≥ Sonnet 86.0 − 2 = 84.0; cost $0.0122 = **40%** of Sonnet's $0.0302 (≤ 60% met); escalation rate 16.5%. The post-hoc sweep: threshold 1.0 → 84.5% at 54% cost |
| G1 | PASS | `make redteam` → `SQL layer: 53/53 attacks blocked` |
| G2 | PASS | `gold FPs 0/550`; `benign tricky allowed 8/8` |
| G3 | PASS | `pytest tests/test_executor.py` (5 passed); bypass layer: `db unchanged=True, stray files=[]`. The only residual is DuckDB `CREATE TEMP VIEW` (in memory, not persisted) |
| G4 | PASS | LLM layer `zip leaks=0`. The warehouse file contains no raw zips at all (masked materialized tables) |
| G5 | PASS | LLM layer: 15 cases, `db unchanged=True`, `prompt leaks=0`, `pass rate=1.0`; the model attempted unsafe SQL in 1 case and it was blocked |
| G6 | PASS | `test_sqlite_timeout`, `test_duckdb_timeout`, `test_row_limit_and_truncation_flag` pass |
| H1 | PASS | `results/ablations.md` columns $/q, p50, p95, in/out tokens, calls/q for every run |
| H2 | PASS | `traces/*.jsonl` (span per step); `make traces` → `results/trace_summary.md`; `--show <trace_id>` waterfall |
| H3 | PASS | `pytest tests/test_serving.py` (5 passed, mock LLM); real smoke runs: `results/logs/smoke_api_real.txt` (answer + clarification), `results/logs/smoke_mcp_real.txt` (governed GMV 2017 = 6,108,492) |
| H4 | PASS | `pytest tests/test_regression_gate.py` (3 passed): current EX 0.833 ≥ 0.75; degraded prompt 0.667 < 0.75 → caught; prompt edit without re-recording → `CassetteMiss` |
| H5 | PASS | `make test` → `115 passed`; `make lint` → `All checks passed!`; Dockerfile, `.github/workflows/ci.yml`, `uv.lock` + `==` pins, py3.12; `make eval-small` / `make eval-full` exist |
| H6 | PASS | `uv run python -m analytics_agent.llm.ledger` → `claude_calls_total: 3397` (≤ 4,000), `claude_cost_usd_total: 10.8415` (Haiku 3,185 calls / $4.63; Sonnet 212 / $6.21). In addition, 231 task attempts were rejected with HTTP 429 during one usage-limit window and retried; they are not billed and not in the ledger |
| I1 | PASS | 70 failures labelled (`eval_sets/bird_failure_labels.jsonl`); 10% (95% CI 5–19%) acceptable answers; 1 verified gold error, 6 ambiguous questions/layouts |
| I2 | PASS | `scripts/error_analysis.py`: per-bucket "solved by" table (filter errors 12/25 → 18/25 with self-correction) |
| J1 | PASS | `du -sh data .llm_cache` → 1.7G + 15M (≤ 3.5 GB) |
