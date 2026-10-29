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

First filled in on 2026-10-29 (v1). Revised the same day after three independent reviews (R1); the
"v1" column records what changed and why. Revision R1 made 0 new model calls.

**35 criteria now: 28 PASS, 2 FAIL (D4, F2), 4 INCONCLUSIVE (C1, C3, D1, D2), 1 corrected (I2).**
- I2's v1 claim was wrong; the corrected analysis passes.
- In hindsight G1 and G4 *failed* in v1: the external review found holes that the v1 suite missed.
  Both pass after the fixes.
- INCONCLUSIVE means one of two things: the observed value sits inside its own noise band around the
  target, or the criterion as written cannot be met at an achievable operating point.

| ID | Result | Evidence | v1 → R1 |
|----|--------|----------|---------|
| A1 | PASS | `make verify-data` → `BIRD gold SQL: 500 queries, 0 errors, 0 empty` | |
| A2 | PASS | `make olist` → all 9 tables `rows=… expected=… OK` (orders 99,441) | |
| A3 | PASS | `verify_olist_sets.py` → 50 gold, 65 variants, 52 interpretations, 20 controls; `all SQL executed OK` (after the HMAC rebuild too) | |
| B1 | PASS | `results/retrieval_recall.json`: BIRD strict recall@6 with FK/lookup expansion = 0.986. *Caveat:* 5.8 of 6.7 tables on average, so little pruning; on sub200 retrieval *lowers* EX (76.5 → 72.0, n.s.) | caveat added |
| B2 | PASS | Olist strict recall@6 = 1.0 | |
| B3 | PASS | pooled 84-table index: recall@10 BM25 0.904 / dense 0.909 / hybrid 0.946 | |
| C1 | INCONCLUSIVE | EX 62.6% vs the dataset card's 59.60% (`eval_sets/provenance/`, rev `5b256b5`): +3.0 pp, inside the ±5 band but also inside the ±3.2 pp single-run noise. Three protocol deviations: an added system prompt, CLI default temperature, our own SQL extraction. It is a sanity check, not a reproduction | PASS → INCONCLUSIVE (reframed) |
| C2 | PASS | (d) vs (a): +107/−54, p = 4e-5 on 500; excluding the 20 prompt-dev questions: 63.1 → 72.9, +99/−52, p = 1.6e-4. Robust to metric choice and to simulated label noise (significance kept in 100% of draws; `results/robustness.json`) | dev20 exclusion and robustness added |
| C3 | INCONCLUSIVE | Run vs run (c) → (d): +15/−7, p = 0.13; 60 of d's first drafts were fresh samples, not replays. Within run (d): correction fired 14 times and fixed 9, +1.8 pp (Wilson CI 0.9–3.4) against a fixed counterfactual (empty or erroring drafts are certainly wrong) | PASS (caveat) → INCONCLUSIVE |
| C4 | PASS | every step in `results/ablations.md`, including those that hurt; few-shot split by template similarity (`results/stats.json` → `fewshot`) | |
| C5 | PASS | sub200: Sonnet (d) 86.0, Haiku (d) 74.5, qwen (d) 13.5; sub200 is easier than the rest by chance (baseline 69.5 vs 58.0, z = 2.7) | subset note |
| D1 | INCONCLUSIVE | EX at exactly 80% coverage depends on breaking ties at agreement 0.8: 80.6–84.4 over 2,000 seeds (mean 82.5, i.e. +6.0). At the achievable threshold ≥ 0.8 (83.5% coverage), the gain is +4.9 (95% CI +1.8 to +8.4), just under +5. Unanimity gives +9.8 (CI +5.2 to +14.6) at 69.5% coverage (`results/stats.json` → `abstention`) | PASS → INCONCLUSIVE |
| D2 | INCONCLUSIVE | AUROC 0.692, bootstrap 95% CI 0.61–0.77, which contains the 0.70 target. 37% of correct/wrong pairs are tied (5-level score). The learned scorer reaches 0.65 out of fold (`results/learned_router.json`) | FAIL → INCONCLUSIVE |
| D3 | PASS | Olist raw arm SC×5: reported, weak (AUROC 0.61), because definition errors are self-consistent: 5 of 8 wrong-definition answers pass a ≥4/5 gate | sharpened |
| D4 | **FAIL** | clarify rate on ambiguous questions: raw arm 60% (target ≥ 70%), semantic arm 35%. False-clarify rate 0% on controls (≤ 15% met) | |
| E1 | PASS | metric-question EX: compiler 29/30 vs raw 14/30 (p = 6e-5). The fair comparison, compiler vs definitions in the prompt, is 29 vs 27 (+2/−0, p = 0.5): **not significant** | fair comparison added |
| E2 | PASS | drift: compiler 0/30 vs raw 8/30 (definitions in the prompt: 1/30); paraphrase groups 4/4 vs 2/4 (a difference of 2 groups) | |
| E3 | PASS | the best pre-planned arm (definitions in the prompt) scores 90.0% ≥ 80%. The 94.0% hybrid is post hoc | |
| F1 | PASS | `docs/img/frontier_cost.png`: 9 configurations on sub200, priced with an explicit token price sheet (fit exactly to the CLI's cost reports), no-caching basis, Pareto line. The latency frontier was dropped (CLI start-up dominates) | replotted |
| F2 | **FAIL** | pre-registered router (escalate if agreement < 0.8): EX 82.0 from the recorded runs (81.5 in a live replay), target ≥ 84.0 → FAIL. Cost 53% of Sonnet on a no-caching basis (40% as recorded) ≤ 60% → met. Oracle router 91.5%. The learned policy does not beat simple rules (`results/learned_router.json`) | cost re-priced; oracle and learned router added |
| G1 | PASS | `make redteam` → `SQL layer: 64/64 attacks blocked`. v1 passed its own 53 attacks, but the v1 validator lets 11 of the current 64 through (6 CTE-shadowing cases, 5 memory bombs; `scripts/redteam_v1_check.py`) | v1 FAIL in hindsight → fixed |
| G2 | PASS | `gold FPs 0/550`; benign look-alikes 8/8; 0 of the agent's previously-correct recorded queries rejected (`results/validator_recorded_fp.json`) | recorded-query check added |
| G3 | PASS | `tests/test_executor.py`; bypass layer: `db unchanged=True, stray files=[]`. Residual: DuckDB `CREATE TEMP VIEW` (in memory) | |
| G4 | PASS (was FAIL) | v1 claimed "0 raw zips", but its md5 tokens were invertible: a dictionary SELECT re-identified 99,441/99,441 customers (reproduced in `confidentiality_layer` on a v1 replica). Now keyed HMAC: the same attack re-identifies 0 (`test_zip_dictionary_attack_reidentifies_nobody`) | **FAIL → fixed → PASS** |
| G5 | PASS | 15 injection cases (recorded against v1 data): DB unchanged, 0 prompt leaks; the model tried unsafe SQL once and it was blocked. n = 15, so the pass rate has a wide CI | |
| G6 | PASS | timeout and row-cap tests, plus new byte limits: `test_memory_bombs_bounded_without_validator` | byte limits added |
| H1 | PASS | `results/ablations.md` columns $/q, p50, p95, tokens, calls/q | |
| H2 | PASS | `traces/*.jsonl`; `results/trace_summary.md` | |
| H3 | PASS | `tests/test_serving.py` (6 passed, including `test_split_vote_is_routed_to_review`). The API now serves the evaluated SC confidence and a route field. Real smoke runs: `results/logs/smoke_*_real.txt` (v1 code) | v1 served a heuristic confidence |
| H4 | PASS | paired gate: fail if net loss ≥ 5 of 30 vs the baseline recording; false-alarm rate 2.1% (v1's absolute threshold: 11.7%); flags the recorded degraded prompt (net −5); power against a true −5 pp regression only 10% at n = 30 (`tests/regression/gate.json`) | redesigned with stated error rates |
| H5 | PASS | `make test` → 149 passed (DB-dependent tests skip without `make data`); `make lint` clean; Dockerfile, CI yaml (included, not yet run on a remote), `uv.lock` + `==` pins, py3.12 | |
| H6 | PASS | ledger: 3,397 Claude calls, $10.84 (CLI `total_cost_usd`); R1 added 0 calls. Plus 231 task attempts rejected with HTTP 429 (not billed, not in the ledger) | |
| I1 | PASS | 70 failures labelled by executing SQL per case, the disputed case re-checked; single annotator. 10% acceptable (CI 5–19%) | |
| I2 | **FAIL as v1 stated; PASS as corrected** | v1's "self-correction moved filter errors 12 → 18" compared separate noisy runs. Corrected within-run attribution: correction fixed 6 of the 25 labelled filter errors and 1 execution error (`docs/error_analysis.md`) | corrected |
| J1 | PASS | `du -sh data .llm_cache cache` → 1.7G + 15M + 2.5M (≤ 3.5 GB) | |
