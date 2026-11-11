# v2 evaluation

All numbers come from `results/v2/` (commands below). Sample sizes are small by design for this build
(owner's time budget); every size is a knob and the full sets are already written and frozen.

| Dataset | Written by | Items | Used in this run | Scale up with |
|---|---|---|---|---|
| `eval_sets/v2/questions_author.jsonl` | repo author, from corpus + schema | 60 | 15 (stratified by type) + 4 definition controls | `make eval-v2 N=all` |
| `eval_sets/v2/questions_codex.jsonl` | ChatGPT via `codex exec`, **blind**: given only the corpus, schema, metric catalog and org chart, not the system | 60 | 15 (stratified) + 4 definition controls | same |
| `eval_sets/v2/questions_holdout.jsonl` | ChatGPT via codex, blind, written **after** the round-1 fixes (sha256 d22537fe6b14...) | 15 | 15 | — |
| `eval_sets/v2/redteam_author.jsonl` | repo author | 20 | 20 | `make redteam-v2 RT_N=...` |
| `eval_sets/v2/redteam_codex.jsonl` | ChatGPT via codex, **blind** (threat model + org chart + ACL rules, no code) | 20 | 20 | same |
| `eval_sets/v2/redteam_holdout.jsonl` | ChatGPT via codex, blind, written after the fixes and told the system had been hardened (sha256 b76fd0498cc5...) | 20 | 20 | — |

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

## Results, full run (round 2: all 135 questions, after the fixes)
`make eval-v2 N=all`. The author half was used for development; the two codex halves were not.
Re-recorded after the answer output-contract fix (INCIDENTS I-8): before it, 57/135 copilot and 88/135 docs-only
answers mentioned a prompt section name (SOURCES / DATA / NOTICES); after it, 0/135 for every system. Accuracy
before -> after: copilot 81% -> 81%, codex half 65% -> 67%, holdout 73% -> 67%, docs-only 30% -> 31%.
Red team re-run on the new answers (round 3): 0/60 leaks, holdout 0/20.

| System | All 135 | Author (60) | Codex-blind (60) | Blind holdout (15) | docs | sql | mixed | clarify | Route |
|---|---|---|---|---|---|---|---|---|---|
| **copilot** | **81%** (CI 73–87%) | 98% | 67% | 67% | 93% | 69% | 78% | 100% | 99% |
| docs-only RAG | 31% | 32% | 30% | 33% | 93% | 0% | 0% | 0% | 33% |
| v1 SQL agent (reference) | 25% | 27% | 25% | 20% | 0% | 63% | 0% | 0% | 39% |

- Paired exact McNemar: copilot vs docs-only 67 wins / 0 losses; vs v1 84 / 9; both p < 0.001.
- Copilot on the codex half by type: docs 90%, sql 42%, mixed 67%, clarify 100%. Counting only the key value of
  each SQL answer, the codex half is 77%. SQL/mixed misses on all 135: 6 right key value without the requested
  helper columns, 6 different row breakdown, 4 different values, 4 no rows (not expressible), 2 document part
  judged incomplete.
- Recall@5 100%; delivered citations 100% within the allowed context; every answered docs/mixed question cites.
- **Metric definitions (Q6).** Asked back on 9/9 ambiguous questions; asked back on 0/9 questions that state the
  definition (was 1/8 before INCIDENTS I-7; the fix is a general signal list, checked on 14 unseen paraphrases).
  Of those 9, 6 return the right result; 3 return the right figure in a different shape.
- Cost of this round: 1,112 ledger calls in total for the project so far, $0.86 list-price equivalent
  (561 copilot, 292 v1 baseline, 259 judge).

## Results, first run (N = 30: 15 author + 15 codex-blind, before the definition fix)
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
- **Current (n = 30, stratified).** 10 answers the judge called correct, 10 partially correct, 10 incorrect, drawn at
  random (fixed seed) from the judged answers of all three systems. Second grader: ChatGPT via codex, read-only, blind
  to the first grader and to which system wrote the answer. Agreement 20/30, **Cohen's kappa 0.5** (3 classes);
  correct-vs-not agreement 24/30, kappa 0.609.
- Where they disagree: 6 answers the judge called partially correct were correct for the second grader; 3 partially
  correct vs incorrect; 1 incorrect vs partially correct. The judge (Claude Haiku 5.5) is the stricter grader, so the
  docs/mixed accuracies are more likely understated than overstated. Disagreements were not adjudicated one by one
  this round.
- **Earlier (n = 10).** 9/10 agreement, kappa 0.615, but 8 of the 10 were answers the judge called correct, which makes
  the number unstable; superseded by the stratified sample.

## Permission red team (`results/v2/redteam_round*.json`)
- **Round 1 (before any red-team-driven fix): 0/40 leaks** (0/20 author, 0/20 codex-blind).
- **Oracle sanity check**: the 10 revocation/cache/deletion scenarios re-run with the query-time recheck,
  live narrowing and delivery check switched off: **8/10 leak** (the other 2: a deactivated user is denied at
  identity mapping, which reads the live directory; and an ACL change with `sync: full`, where the index has
  already caught up). So the oracle does detect leaks when they happen.
- **Final round** (after the quality fix I-2 and harness fixes): 0/40.
- **Round 2, after the acceptance-review fixes (I-4..I-7): 0/60**, including **0/20 on a fresh holdout batch**
  written blind by codex after the fixes (multi-turn escalation, differencing, multi-member channels, injected
  'official' metric definitions, identity edge cases). Many holdout answers refuse in-scope parts as well
  (over-denial; not scored).
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
