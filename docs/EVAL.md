# v2 evaluation

All numbers come from `results/v2/` (commands below). Sample sizes are small by design for this build
(owner's time budget); every size is a knob and the full sets are already written and frozen.

| Dataset | Written by | Items | Used in this run | Scale up with |
|---|---|---|---|---|
| `eval_sets/v2/questions_author.jsonl` | repo author, from corpus + schema | 60 | 15 (stratified by type) + 4 definition controls | `make eval-v2 N=all` |
| `eval_sets/v2/questions_codex.jsonl` | ChatGPT via `codex exec`, **blind**: given only the corpus, schema, metric catalog and org chart, not the system | 60 | 15 (stratified) + 4 definition controls | same |
| `eval_sets/v2/redteam_author.jsonl` | repo author | 20 | 20 | `make redteam-v2 RT_N=...` |
| `eval_sets/v2/redteam_codex.jsonl` | ChatGPT via codex, **blind** (threat model + org chart + ACL rules, no code) | 20 | 20 | same |

Gold for SQL items: the item's gold SQL run as the owner with an explicit `dept_code IN (<asker's departments>)`
restriction from the policy model (`scripts/v2/build_gold.py`), frozen in `gold_*.json` with the set's sha256.
The codex half was not used for development; the author half was (one bug, INCIDENTS I-2, was found there).

## Metrics (fixed before the first scored run; `src/analytics_agent/eval/copilot_eval.py`)
- **sql**: deterministic row match against gold (same row count; each gold row matched to a distinct system row
  whose numbers cover the gold numbers one-to-one within 1%, and whose labels include the gold labels; tightened
  after the acceptance review showed the first version accepted one system value for two gold cells; scores
  did not change). Column order inside a row is not checked. Secondary "key value" score checks only the last numeric column.
- **docs**: LLM judge (Claude Haiku 5.5) against reference answer + key facts; only "correct" counts.
- **mixed**: SQL match AND judge correct. **clarify**: the system asked which definition.
- Route accuracy, recall@5 of gold documents, citation validity, clarification rates.

## Results (N = 30: 15 author + 15 codex-blind)
| System | All | Author half | Codex-blind half | docs | sql | mixed | clarify | Route |
|---|---|---|---|---|---|---|---|---|
| **copilot** (Haiku 5.5) | **93%** (28/30, CI 79–98%) | 100% | 87% | 8/8 | 6/6 | 6/8 | 8/8 | 97% |
| docs-only RAG (same authorised evidence) | 27% | 27% | 27% | 8/8 | 0/6 | 0/8 | 0/8 | 27% |
| v1 SQL-only agent (DuckDB copy, no permissions; historical reference) | 13% | 20% | 7% | 0/8 | 4/6 | 0/8 | 0/8 | 17% |

Paired exact McNemar: copilot vs docs-only 20 wins / 0 losses, p < 0.001; vs v1 24 / 0, p < 0.001.
Recall@5 of gold documents 100%; 100% of delivered citations were in the allowed context; 100% of answered
docs/mixed questions carried at least one citation.

**Metric-definition conflicts.** Asked back on 8/8 ambiguous questions. On 8 controls that name a definition,
5 were right, 1 was a false clarification (12.5%, above the 10% target: cx-q57 says "counting only requests
with status Completed", which no selector phrase matches), 2 computed the right figure in the wrong shape
(cx-q59 split the result by status; cx-q60 asked for helper counts the governed metric does not return).

**Errors (copilot).** cx-q42 and cx-q44 (mixed): numbers right, judge marked the document part partially
correct. cx-q44 is a judge error (adjudicated below); cx-q42 missed one key fact (the equity framing).

## Judge calibration (`results/v2/judge_calibration.json`)
Second grader ChatGPT (codex, read-only, blind to the first grader) on 10 copilot answers (the 2 the judge did
not mark correct + the first 8 it did). Agreement 9/10, Cohen's kappa 0.615. The disagreement (cx-q44) is
adjudicated "correct": the answer has both key facts and the judge's own reason lists none missing.
Adjudicated copilot accuracy would be 29/30; the headline keeps the pre-registered judge verdicts.
Caveat: 10 items, 8 of one class: kappa is unstable at this n.

## Permission red team (`results/v2/redteam_round*.json`)
- **Round 1 (before any red-team-driven fix): 0/40 leaks** (0/20 author, 0/20 codex-blind).
- **Oracle sanity check**: the 10 revocation/cache/deletion scenarios re-run with the query-time recheck,
  live narrowing and delivery check switched off: **8/10 leak** (the other 2: a deactivated user is denied at
  identity mapping, which reads the live directory; and an ACL change with `sync: full`, where the index has
  already caught up). So the oracle does detect leaks when they happen.
- **Final round** (after the quality fix I-2 and harness fixes): 0/40.
- **Not done** (rescoped): a post-fix holdout of fresh blind attacks. With 0 first-round leaks there were no
  red-team-driven fixes, so the 40 are still unseen attacks for the shipped code.
- **The red team missed two delivery leaks** that the independent acceptance review then reproduced: a
  channel audience past the first page of members, and a revocation during the audience lookup (INCIDENTS I-4,
  I-5). Both are fixed with tests; the oracle now takes readers from the emulator, not from the bot.
- Over-denial is visible in the transcripts (several in-scope parts of multi-part attack questions were refused
  because retrieval returned nothing after the recheck); it is not scored here.

## Revocation-lag window (`results/v2/lag_window.json`)
5 revocations x 4 delays (0, 60, 299, 301 s) with a 300 s sync lag, no LLM involved. Recheck ON: 0 document and
0 SQL leaks in 20 probes. OFF: 15/15 probes inside the window leak, 0 after it.

## Cost
368 live model calls (269 copilot, 64 v1 baseline, 35 judge) at $0.32 list-price equivalent
(`results/v2/llm_ledger.jsonl`; the CLI reports cost on the subscription, nothing was billed).

## Reproduce
`make eval-v2 N=15` (live, cached), `make redteam-v2`, `make lag-experiment`; offline re-check: `make check`.
