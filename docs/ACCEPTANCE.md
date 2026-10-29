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
| F2 | Routed system (Haiku first, escalate to Sonnet on low confidence), measured by actually running the router on sub200: EX ≥ Sonnet-only EX − 2 pp, at ≤ 60 % of Sonnet-only cost per question | `results/routing.json` |

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

(Filled in at the end of the build.)
