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
  definition has hand-written, executed gold SQL and executed "plausible variant" definitions, such
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
| raw | 64.0% | 46.7% | 90.0% | **26.7%** (8/30) | 2/4 | 0.00164 | 11.4 |
| glossary | 90.0% | 90.0% | 90.0% | 3.3% (1/30) | 4/4 | 0.00193 | 10.6 |
| semantic | 80.0% | **96.7%** | 55.0% | **0%** | 4/4 | 0.00188 | 10.9 |
| hybrid | **94.0%** | **96.7%** | 90.0% | **0%** | 4/4 | 0.00272 | 20.9 |

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

## What we learned
1. **Most of the raw-SQL error on business questions is definitional, not SQL skill.**
   - Raw was 90% on non-metric questions but 46.7% on metric questions.
   - Telling the model the definitions (glossary) recovers most of the gap, from 46.7% to 90.0%.
2. **Compiling through governed metrics removes the remaining drift.**
   - Metric EX was 96.7%, with 0 drift and all paraphrases consistent, because the model never
     writes metric math. It picks `gmv` and the compiler supplies the one definition.
   - Glossary still let one definition slip: it compared timestamps instead of dates for lateness.
3. **The semantic prompt's raw-SQL fallback was worse on non-metric questions (55% vs 90%).**
   - Reading the failures showed two causes. Its "use governed definitions for any business term"
     instruction made it add the valid-order filter to questions that said "any status". It also
     lacked the "return exactly the requested columns" rule and added helper columns.
   - That is why the **hybrid** routes non-metric questions to the documented raw agent: 94% overall,
     0 drift.
   - *Caveat:* the hybrid was designed after seeing these failures, on the same 50 questions. Treat
     94% as optimistic until it is confirmed on new questions.
   - The cost is ~1.4× per question, because non-metric questions make two calls. Latency is
     also higher; a cheap intent classifier in front would remove most of both.
4. **Governance must be centralized, not just written down.**
   - A sister project (`a sister project metrics file`) independently wrote
     "governed" definitions for the same 12 metric names on the same data. Re-implemented on our
     warehouse (`scripts/compare_sister_metrics.py`), they give:

     | metric | ours | sister repo | difference |
     |---|---|---|---|
     | cancellation_rate | 0.63% (canceled only) | 1.24% (canceled + unavailable) | **+97%** |
     | nps_style_score | 34.8 (%5★ − %1–3★, all review rows) | 44.5 (%5★ − %1–2★, latest review per order) | **+28%** |
     | avg_review_score | 4.086 | 4.116 (latest review per order) | +0.7% |
     | orders | 98,207 | 98,199 (orders with ≥1 item) | −0.01% |

   - Two careful teams produced two "official" NPS numbers 28% apart. The lesson is that a semantic
     layer only helps if there is exactly one of it. Our experiment's conclusions do not depend on
     which definition is chosen, only on there being one designated definition.

## Ambiguity: when the definition is not settled at all
See `results/ambiguity__haiku.json` and the README section "Knowing when not to answer". Some
questions really are ambiguous, for example "What was revenue last quarter?": freight in or out,
purchase or delivery date, last *complete* quarter or the partial one. There the agent should ask,
not guess. The governed layer resolves 2 of the 20 curated ambiguous questions (AOV and
cancellation rate) by definition. The other 18 need a clarifying question.

## Limitations
- 50 questions from a single author (the golden set was written for this repo). EX differences of
  one or two questions are within noise; the drift and consistency effects are large.
- Haiku only. Stronger models might drift less in the raw arm, but definitions are company-specific
  knowledge no model can guess.
- Our compiler supports sum/avg/count-distinct/ratio metrics over four governed models, with
  dimensions, filters, time grains and windows. Growth rates and segment comparisons fall back to
  SQL. That is the usual frontier of real semantic layers as well.
