# Error analysis: what fails, which fix moved which bucket, and how much is label noise

**Sample.** The zero-shot baseline (a) fails 187 of the 500 BIRD Mini-Dev questions. Exactly 70 of
those 187 were drawn at random (seed 2026) by `scripts/dump_failures.py`. Each was categorized by
hand with a taxonomy fixed beforehand. For every case the reviewer re-ran SQL on the database to find
the actual cause: column values, join fan-out, and whether gold's reading of the question holds.
- Labels: `eval_sets/bird_failure_labels.jsonl`, each with a one-line evidence note.
- Join with later runs: `uv run python scripts/error_analysis.py` → `results/error_analysis/summary.json`.
- *Process note.* Labels follow this taxonomy, and every case was checked by executing SQL; the
  disputed cases (for example bird_093) were re-checked. Single annotator.

## Taxonomy and counts (n = 70)

| bucket | n | share | typical root cause (from the notes) |
|---|---|---|---|
| filter_error | 25 | 36% | literal or format mismatch with the stored data: CDS codes stored with a leading zero, 'YYYY/MM/DD' dates, lower-case 'azorius', comma-list columns compared with `=`, numbers stored as text, 'No Colour' vs 'Unknown' |
| computation_error | 10 | 14% | date math, integer division, rounding, formula |
| output_shape | 8 | 11% | right facts in a different layout (rows vs. one wide row), id instead of name, tie handling |
| aggregation_error | 7 | 10% | counting printings or ruling rows instead of distinct cards (fan-out), blank-sex values counted as a group |
| join_error | 7 | 10% | wrong path, missing zero rows for the population base |
| schema_linking | 4 | 6% | wrong column (`type` vs `types`), wrong table |
| question_ambiguity | 3 | 4% | the question supports both readings |
| null_handling | 3 | 4% | |
| execution_error | 2 | 3% | |
| gold_error | 1 | 1% | |

## Label noise: how much of the "failure" is the benchmark's fault?
- 7 of the 70 failures (10%; 95% CI 4.9–19.2%) are answers a careful analyst would accept:
  - 1 gold label is wrong (bird_093). Gold groups by the full type line, so "Instant" wins; in MTG
    "card type" is the `types` column, where Creature has 71 rows.
  - 4 come from questions that support both readings, for example `totalSetSize` vs counted card rows.
  - 2 are the same facts in a layout the question did not fix.
  - 1 more is ambiguous but was judged unacceptable.
- Scaled to all 187 failures, that is about 19 questions (9–36). **Strict EX therefore understates the
  baseline by about 3.7 pp (1.8–7.2 pp).** That band is wider than several ablation steps below.
  Before trusting a 1–2 pp improvement on this benchmark, re-label the questions whose outcome
  changed.
- Context. The 2026 "Mini-Dev 1024" release was re-annotated by a human-in-the-loop review, and it
  shows. Wretblad et al. (ACL 2024, [arXiv:2402.12243](https://arxiv.org/abs/2402.12243)) found that
  49% of the *original* BIRD dev questions in the financial domain contained errors. In our sample of
  the cleaned release, the reviewers verified only one gold label as clearly wrong. **Most remaining
  disagreement is ambiguity, not wrong labels.** That is also how real business questions behave,
  which is why the agent has a clarification mode.

## Which fix moved which bucket
Each cell shows how many of the bucket's labelled failures are correct under that configuration.
Configs (b)–(d) are separate full runs, so each cell is "solved by config X", not an increment.

| bucket | n | (b) our prompt + retrieval | (c) + few-shot | (d) + self-correction | Sonnet (d)* | Haiku SC×5* |
|---|---|---|---|---|---|---|
| filter_error | 25 | 10/25 | 12/25 | **18/25** | 5/5 | 3/3 |
| computation_error | 10 | 8/10 | 5/10 | 6/10 | 1/2 | 0/1 |
| output_shape | 8 | 4/8 | 3/8 | 3/8 | 2/2 | – |
| aggregation_error | 7 | 3/7 | 4/7 | 5/7 | 1/3 | 0/1 |
| join_error | 7 | 2/7 | 3/7 | 3/7 | 1/1 | – |
| schema_linking | 4 | 2/4 | 1/4 | 1/4 | 2/2 | 1/2 |
| question_ambiguity | 3 | 2/3 | 1/3 | 2/3 | – | – |
| null_handling | 3 | 3/3 | 3/3 | 3/3 | 1/1 | – |
| execution_error | 2 | 0/2 | 0/2 | 1/2 | – | – |
| gold_error | 1 | 0/1 | 0/1 | 0/1 | – | – |
| **all** | 70 | 34/70 | 32/70 | **42/70** | 13/16 | 4/7 |

\* Sonnet and SC were run only on the frozen 200-question subset, which contains 16 of the 70
labelled failures.

How to read it:
- **Filter errors** are the biggest bucket and the one the pipeline was designed against.
  - They move from 10–12 solved, without execution feedback, to **18 of 25** with self-correction.
  - The mechanism is value grounding: on an empty result the corrector sees the column's real values
    (`value_hints.py`), so it learns that CDS codes have a leading zero or that the value is 'No Colour'.
- **Computation errors** are fixed mostly by our prompt in (b), which asks for a short plan first. Few-shot examples do
  not help them; examples from the old BIRD dev annotations sometimes teach a different formula style.
- **Output-shape and join errors barely move.** These are "the model understood the question
  differently" errors. A bigger model fixes them (Sonnet solves 4 of 4 in the subset), and so does a
  clarifying question. More retries do not.
- **Self-consistency rarely fixes errors.** It is valuable as a *confidence signal* (see the
  abstention results), not as an accuracy booster.

## How self-correction behaves, measured exactly
Inside run (d), the self-correction loop fired on 14 of 500 questions (2.8%):

| trigger | fixed | not fixed |
|---|---|---|
| empty result | 6 | 1 |
| execution error | 2 | 2 |
| all-NULL result | 1 | 2 |

- An empty or erroring draft is certainly wrong, because every gold result in this release is
  non-empty. So the within-run gain is exactly **+9 questions (+1.8 pp)**, or +7 in the worst case
  if both unfixed all-NULL drafts had been right.
- Run-level (c)→(d) differences, +15/−7, are larger than this. The extra movement is sampling noise:
  58 of d's 500 first drafts were regenerated rather than replayed from (c)'s cache, because (c), (d)
  and the SC run were partly concurrent and interrupted by a usage-limit restart. See
  `results/noise_floor.json` for the measured run-to-run noise.
