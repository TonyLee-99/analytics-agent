# Governed semantic layer vs. raw SQL: a controlled experiment (Olist)

**Question.** When an LLM agent answers business questions, does it help to ground it in governed
metric definitions? And is it enough to *tell* the model the definitions, or must it also be stopped
from re-implementing them? Companies argue for a semantic layer with exactly these claims: dbt
Semantic Layer/MetricFlow, Airbnb Minerva, LookML, and Uber QueryGPT's curated "workspaces".

**Setup.** Fixed for every arm:
- Model: Claude Haiku 5.5. Self-correction: 2 rounds. Schema: the full Olist schema with table and
  column docs.
- Questions: the same 50 questions in `eval_sets/olist_golden.jsonl`.
  - 30 depend on a governed metric. These are phrased the way a PM speaks ("What was our GMV in
    2017?"), without the definition.
  - 20 do not (lookups, multi-joins, windows, cohorts).
- Business truth = the governed definitions in `src/analytics_agent/semantic/metrics.yaml`. Each
  definition has gold SQL written for this repo (every query executed and sanity-checked) and executed "plausible variant" definitions, such
  as GMV with freight, payment-based revenue, including canceled orders, or `customer_id` instead of
  `customer_unique_id`.
- EX = result-set equality, with floats compared at 4 significant digits.

| Arm | What the model gets | Who writes the metric math |
|---|---|---|
| raw | schema + column docs | the LLM |
| glossary | raw + the governed metric definitions pasted into the prompt as text | the LLM (told the definition) |
| semantic | a catalog of governed metrics/dimensions; it emits a JSON metric query, and `semantic/compiler.py` compiles it to SQL; raw SQL only as a fallback | the compiler |
| hybrid | semantic first; if the model says "not expressible as metrics", the question goes to the glossary agent | the compiler, or the LLM for non-metric questions |

Reproduce: `make semantic` → `results/semantic_experiment__haiku.json`. All four arms are recorded
runs; hybrid reuses the cached responses of the other arms.

## Results (Haiku, n = 50)

| Arm | EX all | EX metric Qs (n=30) | EX non-metric (n=20) | Definition drift on metric Qs | Paraphrase groups consistent | $/question | p95 s |
|---|---|---|---|---|---|---|---|
| raw | 64.0% | 46.7% | 90.0% | 26.7% (8/30) | 2/4 | 0.00164 | 11.4 |
| glossary | **90.0%** | 90.0% | 90.0% | 3.3% (1/30) | 4/4 | 0.00193 | 10.6 |
| semantic | 80.0% | 96.7% | 55.0% | 0% | 4/4 | 0.00188 | 10.9 |
| hybrid (*post hoc*) | 94.0% | 96.7% | 90.0% | 0% | 4/4 | 0.00272 | 20.9 |

**Definition drift** is the share of metric questions whose answer *exactly equals a known
non-governed definition*. This is not random error: it is a plausible, different number. In the raw
arm the drift was:

| Raw arm picked... | times |
|---|---|
| counted canceled/unavailable orders (repeat rate ×3, order count) | 4 |
| AOV from `payment_value` (includes installment interest and freight) instead of item price | 2 |
| GMV including freight | 1 |
| delivery days as calendar-day difference instead of fractional days | 1 |

**Paraphrase consistency.** Each group asks the same question three ways. The raw arm answered the GMV-2017 group
with two different numbers: "What was our GMV in 2017?" gave 6,108,492 BRL, while "Give me total GMV
for orders placed between January 1 and December 31, 2017" gave GMV *including freight*, 16% higher.
An executive who asks twice gets two numbers. That is the failure mode semantic layers exist to
remove.

## Paired tests (`results/stats.json` → `semantic`)
| comparison | questions | correct A → B | B-only / A-only | exact McNemar p |
|---|---|---|---|---|
| raw → glossary | metric (30) | 14 → 27 | 14 / 1 | 0.001 |
| glossary → semantic | metric (30) | 27 → 29 | 2 / 0 | 0.5 |
| raw → semantic | metric (30) | 14 → 29 | 15 / 0 | 6e-5 |
| glossary → hybrid | all (50) | 45 → 47 | 2 / 0 | 0.5 |
| semantic → hybrid | all (50) | 40 → 47 | 7 / 0 | 0.016 |

## What we learned
1. **The fair comparison is glossary vs compiler, and it is not significant here.**
   - *Telling* the model the governed definitions fixes most of the problem: 14 → 27 of 30,
     p = 0.001.
   - *Compiling* them adds 2 questions (p = 0.5), removes the last drift case, and makes paraphrase
     consistency structural rather than a matter of the model listening.
   - Raw vs compiler (46.7% → 96.7%) mostly measures "the raw arm was never told our conventions".
     That is the real business problem, but it is not evidence that the compiler beats documentation.
2. **The sharp result: definition errors pass the confidence gate.**
   - The raw arm's 8 wrong-definition answers were produced consistently: 5 of the 8 had at least
     4 of 5 samples agreeing on the result, against 22 of 32 correct answers.
   - A confidence gate tuned on SQL errors cannot see them. On BIRD, where errors are SQL errors, the
     same gate does separate right from wrong (89% of correct answers pass vs 66% of wrong ones).
   - Governed definitions are the only measured control for this error class, whether in the prompt
     or compiled.
3. **The semantic prompt's raw-SQL fallback was worse on non-metric questions (55% vs 90%).**
   - Its "use governed definitions for any business term" instruction made it add the valid-order
     filter to questions that said "any status".
   - It also lacked the "return exactly the requested columns" rule.
   - So the semantic arm differs from the others in more than one factor: its prompt differs too.
   - The hybrid routes non-metric questions to the glossary agent. It was designed after reading
     these failures on the same 50 questions, so its 94% is post hoc, and vs glossary it is +2/−0
     (p = 0.5).

## Ambiguity: when the definition is not settled at all
See `results/ambiguity__haiku.json`. Some questions are genuinely ambiguous: "What was revenue last
quarter?" (freight in or out, purchase or delivery date, last *complete* quarter or the partial
one). The agent should ask, not guess.
- The raw arm asked on 60% of the 20 curated ambiguous questions.
- The semantic arm asked on only 35%: it answered with the governed default instead (for example,
  top category by GMV). For a governed company that can be right, but the answer should then say
  which definition it used.
- Neither asked on any of the 20 clear controls.

## Limitations
- 50 questions, 30 of them metric questions. Definitions, gold SQL, variants and compiler come from
  one author, so the governed definitions are partly self-consistent by construction.
- Paraphrase consistency: "4/4 vs 2/4" is a difference of 2 groups.
- The compiler fixes output column order (dimensions, period, metrics), and gold was written to the
  same convention. The compiler arm therefore gets formatting alignment for free.
- Haiku only. Stronger models might drift less in the raw arm, but definitions are company-specific
  knowledge no model can guess.
