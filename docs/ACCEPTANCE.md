# v2 acceptance criteria (governed data and knowledge copilot)

Written 2026-11-11, after the design (docs/DESIGN.md) and **before any v2 code or evaluation**.
Revised once the same day after the independent design review (docs/DESIGN.md section 6), still before any code. Targets are not changed after results come in; a missed target is reported with its real number.
v1's criteria and results are unchanged in `docs/v1/ACCEPTANCE.md`. The Result column is filled in
at the end.

## A. Data and corpus
| ID | Criterion | Verify | Result |
|----|-----------|--------|--------|
| A1 | Postgres holds the bounded 311 window; row count per department equals the source DuckDB count for the same window exactly; min/max created_at inside the window | `make pg-verify` | |
| A2 | >= 60 real public City of Chicago documents; each has source URL, fetch time, sha256 and a licence/terms note; the fetcher sends a descriptive User-Agent, honours a >= 1 s crawl delay, backs off on 429/5xx and caches | `pytest tests/v2/test_corpus.py` | |
| A3 | Document ACLs come only from `config/doc_acl.yaml`, labelled Simulated; >= 20% of documents are restricted | same | |

## P. Permissions (the hard problem)
| ID | Criterion | Verify | Result |
|----|-----------|--------|--------|
| P1 | RLS: for every Simulated user and a set of probe queries, the rows returned equal exactly the oracle's rows for the user's departments (no extra rows, no missing rows); forged settings / role switches with the validator bypassed fail; the gateway role is not superuser, not owner, has no BYPASSRLS and no direct table privilege | `pytest tests/v2/test_rls.py` | |
| P2 | Retrieval filters before ranking: the scorer never receives a chunk outside the allowed set; BM25 statistics come from the allowed set only | `pytest tests/v2/test_retrieval_acl.py` | |
| P3 | Revocation lag: with sync lag and query-time recheck ON, 0 leaks during the lag window; with recheck OFF the same scenario leaks (proves the window is real); both numbers reported | `make lag-experiment` -> `results/v2/lag_window.json` | |
| P4 | Answer cache and embedding cache are invalidated on revocation and deletion | `pytest tests/v2/test_caches.py` | |
| P5 | Deletion propagation: after a source delete and a sync, no trace of the doc remains in chunk text, BM25 postings, vectors, embedding cache or answer cache | same | |
| P6 | Identity-mapping failures (unknown Slack user, no email, unknown email, deactivated, ambiguous, bot) are denied 100% | `pytest tests/v2/test_identity.py` | |
| P7 | Red team (40 = 20 author + 20 written blind by codex): first-round leak rate reported before fixes; after fixes 0/40 leaks; every first-round leak is in docs/INCIDENTS.md; plus a post-fix holdout of 10 fresh codex attacks, run once and reported as is; over-denial reported separately | `make redteam-v2` -> `results/v2/redteam.json` | |

## Q. Answer quality (120 questions, 60 author + 60 codex-blind, reported separately)
| ID | Criterion | Verify | Result |
|----|-----------|--------|--------|
| Q1 | Overall answer correctness (deterministic for SQL, LLM judge for docs/mixed, calibrated in Q7) >= 70%, and >= 50% in every route stratum | `make eval-v2` -> `results/v2/eval.json` | |
| Q2 | Router accuracy (docs / sql / both / clarify) >= 85% | same | |
| Q3 | 100% of citations that reach the user point to a chunk that was in the allowed context, and >= 90% of answered docs/mixed questions carry at least one valid citation | same | |
| Q4 | Doc retrieval recall@5 (share of gold docs in top 5, averaged per question) >= 0.80 on doc and mixed questions | same | |
| Q5 | The copilot beats the docs-only RAG baseline (same authorised evidence) and the v1 SQL-only agent (historical reference, no permissions) on overall correctness, paired test reported | same | |
| Q6 | Conflicting metric definitions: asks on >= 80% of the conflict questions (no selector phrase), false clarification <= 10% on clear controls (selector phrase present) | same | |
| Q7 | Judge calibration: second independent grader (codex) on 30 answers; Cohen's kappa reported with every disagreement adjudicated | `results/v2/judge_calibration.json` | |

## D. Delivery and operations
| ID | Criterion | Verify | Result |
|----|-----------|--------|--------|
| D1 | The Bolt app, run unchanged against the Slack emulator: mention -> answer with citations; 👍/👎 + reason recorded with the trace id; redelivered events answered once; unmapped identity denied; restricted answers in a channel with unentitled members go ephemeral | `pytest tests/v2/test_slack.py` | |
| D2 | FastAPI `/v2/ask` and the MCP tools answer through the same copilot and enforce identity | `pytest tests/v2/test_serving_v2.py` | |
| D3 | Every answer writes a trace with: user, DB role, live departments, route, retrieved/denied/rechecked counts, SQL, tokens, latency, cost | `pytest tests/v2/test_trace.py` | |
| D4 | `make check` runs offline (replay backend, zero LLM calls) and includes the eval gate; CI runs it | `make check` | |
| D5 | README headline numbers equal `results/v2/*.json` (test); v1 results still reproduce from recorded runs | `pytest tests/test_readme_numbers.py`, `make reproduce-results` | |
| D6 | Static demo page built from recorded real runs, screenshotted with Playwright | `make site` | |
