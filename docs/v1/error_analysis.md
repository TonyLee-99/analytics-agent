# Error analysis: what fails, how much is label noise, and do the conclusions survive it

**Sample.** The zero-shot baseline (a) fails 187 of the 500 BIRD Mini-Dev questions.
`scripts/dump_failures.py` drew 70 of them at random (seed 2026).

**How the labels were made.** The 70 failures were labelled in
two batches of 35, using a taxonomy fixed beforehand and running SQL on the database for every case to find the
actual cause (column values, join fan-out, whether gold's reading holds). The one disputed gold-error case (bird_093) was
re-checked. These are careful per-case labels, but there is a single annotator and no inter-annotator agreement was measured.
- Labels: `eval_sets/bird_failure_labels.jsonl`, each with a one-line evidence note.
- Summary: `uv run python scripts/error_analysis.py` → `results/error_analysis/summary.json`.

## Taxonomy and counts (n = 70)

| bucket | n | share | typical root cause (from the notes) |
|---|---|---|---|
| filter_error | 25 | 36% | literal or format mismatch with the stored data: CDS codes with a leading zero, 'YYYY/MM/DD' dates, lower-case 'azorius', comma-list columns compared with `=`, numbers stored as text, 'No Colour' vs 'Unknown' |
| computation_error | 10 | 14% | date math, integer division, rounding, formula |
| output_shape | 8 | 11% | right facts in a different layout (rows vs one wide row), id instead of name, ties |
| aggregation_error | 7 | 10% | counting printings or ruling rows instead of distinct cards (fan-out); blank values counted as a group |
| join_error | 7 | 10% | wrong path; missing zero rows for the population base |
| schema_linking | 4 | 6% | wrong column (`type` vs `types`), wrong table |
| question_ambiguity | 3 | 4% | the question supports both readings |
| null_handling | 3 | 4% | |
| execution_error | 2 | 3% | |
| gold_error | 1 | 1% | |

## Label noise
- **Acceptable failures.** 7 of 70 failures (10%; 95% CI 4.9–19.2%) are answers a careful analyst
  would accept:
  - 1 wrong gold label: bird_093, which groups by the full type line. In MTG, "card type" is the
    `types` column.
  - 4 questions that support both readings.
  - 2 cases of the same facts in a layout the question did not fix.
- **What that does to EX.** Scaled to all 187 failures, strict EX understates the baseline by about
  3.7 pp (1.8–7.2 pp).
- **What was not measured.** Only *failures* were audited. Wrong SQL that matches gold by
  coincidence, which would make EX overstate accuracy, was not measured.
- **Context.** The original BIRD release had heavy label noise: Wretblad et al., ACL 2024, found
  errors in 49% of the financial-domain dev questions. In this sample of the re-annotated 2026
  release, only one gold label was verified wrong. Most of the remaining disagreement is ambiguity.

## Do the ablation conclusions survive label noise and the choice of metric?
`uv run python -m analytics_agent.eval.robustness` → `results/robustness.json`.

**Metric choice.** Every recorded prediction was re-executed and rescored four ways: official set
equality, multiset, ordered list, and float tolerance 1e-6. Rescoring reproduces every stored EX
exactly (an assertion checks this). Across the four metrics:
- no paired comparison changes sign or significance;
- deltas move by at most 1.5 pp;
- ordered comparison costs every run 2–4 pp, because some questions do not fix an order.

**Label noise.** The audit gives a posterior Beta(8, 64) for the share of failures that are actually
acceptable. In each of 2,000 simulations, every failure is turned into a pass with that probability
and every paired comparison is recomputed. Two noise models are used: *independent* (per system and
question) and *shared* (acceptability is a property of the question).

| comparison | Δ observed | Δ corrected, independent (95% interval) | significance kept, independent / shared |
|---|---|---|---|
| (a) → (b) prompt + retrieval, n=500 | +7.2, p=0.004 | +6.4 (4.0 to 8.6) | 88% / 99% |
| (a) → (d) full pipeline, n=500 | +10.6, p=4e-5 | +9.5 (6.8 to 11.6) | 100% / 100% |
| Haiku (d) → Sonnet (d), sub200 | +11.5, p=0.001 | +10.3 (7.0 to 12.5) | 98% / 100% |
| (b) → (c) few-shot, n=500 | +1.8, n.s. | +1.6 (−0.6 to 3.6) | sign kept 92% |
| (c) → (d) self-correction, n=500 | +1.6, n.s. | +1.5 (−0.6 to 3.4) | sign kept 91% |
| full schema → retrieval, sub200 | −4.5, n.s. | −4.0 (−7.0 to −0.5) | sign kept 99% |

**Reading.** Label noise shrinks every delta by about 10%, because noise forgives some failures in both
systems. It does not overturn any conclusion. The weakest significant claim is (a)→(b), at 88% under
the pessimistic independent model.

## Which pipeline step fixed which failures, attributed correctly
v1 of this document claimed that self-correction moved filter errors from 12 to 18 of 25. That
compared separate runs: 60 of run (d)'s first drafts were fresh samples, not replays of (c), so most
of that movement was redraw noise. The defensible attribution counts **within run (d)**:
- Self-correction fired on 14 of 500 questions and fixed 9 (+1.8 pp, Wilson CI 0.9–3.4). By trigger:

  | trigger | fixed / fired |
  |---|---|
  | empty result | 6/7 |
  | execution error | 2/4 |
  | all-NULL result | 1/3 |

- An empty or erroring draft is certainly wrong in this release, because every gold result is
  non-empty. So the counterfactual is fixed.
- Among the 70 labelled failures, correction fixed **6 of the 25 filter errors** (5 through
  value-grounded empty-result hints, 1 through an all-NULL hint) and 1 execution error.
- Fixes in other buckets came from different draws or configurations, and are not attributed to a
  mechanism.
- Sonnet solved 13 of the 16 labelled failures that fall in sub200 (`results/error_analysis/summary.json`).
