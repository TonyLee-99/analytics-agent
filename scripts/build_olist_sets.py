"""Build the Olist evaluation sets from hand-written question/SQL definitions.

Every gold / variant / interpretation SQL below was written by hand against the governed views in
data/olist.duckdb (schema `main`). This script only *executes* them to fill in answer previews, row
counts and the relative difference of each definition variant, then writes:

  eval_sets/olist_golden.jsonl        50 business questions with gold SQL (+ definition variants)
  eval_sets/olist_ambiguous.jsonl     20 genuinely ambiguous questions (>= 2 executed interpretations)
  eval_sets/olist_clear_controls.jsonl 20 unambiguous controls (false-clarification rate)

Gold SQL for metric questions follows src/analytics_agent/semantic/metrics.yaml exactly.
Run: uv run python scripts/build_olist_sets.py
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "olist.duckdb"
OUT = ROOT / "eval_sets"

VALID = "o.order_status NOT IN ('canceled', 'unavailable')"
Y2017 = "o.order_purchase_timestamp >= TIMESTAMP '2017-01-01' AND o.order_purchase_timestamp < TIMESTAMP '2018-01-01'"
Y2018 = "o.order_purchase_timestamp >= TIMESTAMP '2018-01-01' AND o.order_purchase_timestamp < TIMESTAMP '2019-01-01'"
DELIVERED = "o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL"
ON_TIME = "CAST(o.order_delivered_customer_date AS DATE) <= CAST(o.order_estimated_delivery_date AS DATE)"

# ---------------------------------------------------------------------------------------------
# Golden set. Paraphrase groups share the same gold SQL.
# ---------------------------------------------------------------------------------------------
GMV_2017 = f"""SELECT sum(oi.price) AS gmv
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
WHERE {VALID}
  AND {Y2017}"""
GMV_2017_VARIANTS = [
    {"label": "gmv_including_freight", "sql": f"SELECT sum(oi.price + oi.freight_value) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2017}"},
    {"label": "payment_value_revenue", "sql": f"SELECT sum(p.payment_value) FROM order_payments p JOIN orders o ON o.order_id = p.order_id WHERE {VALID} AND {Y2017}"},
    {"label": "including_canceled_unavailable", "sql": f"SELECT sum(oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {Y2017}"},
    {"label": "delivered_only", "sql": f"SELECT sum(oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE o.order_status = 'delivered' AND {Y2017}"},
]

ONTIME_2018 = f"""SELECT avg(CASE WHEN {ON_TIME} THEN 1.0 ELSE 0.0 END) AS on_time_delivery_rate
FROM orders o
WHERE {DELIVERED}
  AND {Y2018}"""
ONTIME_2018_VARIANTS = [
    {"label": "timestamp_level_comparison", "sql": f"SELECT avg(CASE WHEN o.order_delivered_customer_date <= o.order_estimated_delivery_date THEN 1.0 ELSE 0.0 END) FROM orders o WHERE {DELIVERED} AND {Y2018}"},
    {"label": "denominator_all_orders", "sql": f"SELECT avg(CASE WHEN o.order_delivered_customer_date IS NOT NULL AND {ON_TIME} THEN 1.0 ELSE 0.0 END) FROM orders o WHERE {Y2018}"},
    {"label": "time_by_delivery_date", "sql": f"SELECT avg(CASE WHEN {ON_TIME} THEN 1.0 ELSE 0.0 END) FROM orders o WHERE {DELIVERED} AND o.order_delivered_customer_date >= TIMESTAMP '2018-01-01' AND o.order_delivered_customer_date < TIMESTAMP '2019-01-01'"},
]

AOV_2018 = f"""SELECT sum(oi.price) / count(DISTINCT oi.order_id) AS aov
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
WHERE {VALID}
  AND {Y2018}"""
AOV_2018_VARIANTS = [
    {"label": "aov_including_freight", "sql": f"SELECT sum(oi.price + oi.freight_value) / count(DISTINCT oi.order_id) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2018}"},
    {"label": "payment_value_per_order", "sql": f"SELECT sum(p.payment_value) / count(DISTINCT p.order_id) FROM order_payments p JOIN orders o ON o.order_id = p.order_id WHERE {VALID} AND {Y2018}"},
    {"label": "avg_item_price_not_order", "sql": f"SELECT avg(oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2018}"},
]

REPEAT = f"""WITH per_customer AS (
  SELECT c.customer_unique_id, count(DISTINCT o.order_id) AS n_orders
  FROM orders o
  JOIN customers c ON c.customer_id = o.customer_id
  WHERE {VALID}
  GROUP BY c.customer_unique_id
)
SELECT avg(CASE WHEN n_orders >= 2 THEN 1.0 ELSE 0.0 END) AS repeat_purchase_rate
FROM per_customer"""
REPEAT_VARIANTS = [
    {"label": "customer_id_instead_of_unique_id", "sql": f"WITH pc AS (SELECT o.customer_id, count(DISTINCT o.order_id) n FROM orders o WHERE {VALID} GROUP BY 1) SELECT avg(CASE WHEN n >= 2 THEN 1.0 ELSE 0.0 END) FROM pc"},
    {"label": "including_canceled_unavailable", "sql": "WITH pc AS (SELECT c.customer_unique_id, count(DISTINCT o.order_id) n FROM orders o JOIN customers c ON c.customer_id = o.customer_id GROUP BY 1) SELECT avg(CASE WHEN n >= 2 THEN 1.0 ELSE 0.0 END) FROM pc"},
    {"label": "distinct_purchase_days", "sql": f"WITH pc AS (SELECT c.customer_unique_id, count(DISTINCT CAST(o.order_purchase_timestamp AS DATE)) n FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {VALID} GROUP BY 1) SELECT avg(CASE WHEN n >= 2 THEN 1.0 ELSE 0.0 END) FROM pc"},
]

GOLDEN: list[dict] = [
    # ---- paraphrase group A: GMV 2017 -------------------------------------------------------
    dict(question="What was our GMV in 2017?", difficulty="easy", category="aggregation", uses_metric=["gmv"],
         paraphrase_group="gmv_2017", gold_sql=GMV_2017, variants=GMV_2017_VARIANTS,
         notes="~6.11M BRL; 2017 is ~45% of total history GMV (13.49M), consistent with 2017 having ~45k of ~99k orders."),
    dict(question="How much gross merchandise value did the marketplace do over calendar year 2017?", difficulty="easy",
         category="aggregation", uses_metric=["gmv"], paraphrase_group="gmv_2017", gold_sql=GMV_2017, variants=GMV_2017_VARIANTS,
         notes="Same answer as olist_001."),
    dict(question="Give me total GMV for orders placed between January 1 and December 31, 2017.", difficulty="easy",
         category="aggregation", uses_metric=["gmv"], paraphrase_group="gmv_2017", gold_sql=GMV_2017, variants=GMV_2017_VARIANTS,
         notes="Same answer as olist_001."),
    # ---- paraphrase group B: on-time 2018 ---------------------------------------------------
    dict(question="What was our on-time delivery rate for orders placed in 2018? Return it as a fraction between 0 and 1.",
         difficulty="medium", category="aggregation", uses_metric=["on_time_delivery_rate"], paraphrase_group="on_time_2018",
         gold_sql=ONTIME_2018, variants=ONTIME_2018_VARIANTS,
         notes="~0.923, lower than 2017 (~0.944); 2018 Feb-Mar had known delivery problems in this dataset."),
    dict(question="Of the orders purchased in 2018 that were delivered, what share arrived by the promised delivery date? (fraction 0-1)",
         difficulty="medium", category="aggregation", uses_metric=["on_time_delivery_rate"], paraphrase_group="on_time_2018",
         gold_sql=ONTIME_2018, variants=ONTIME_2018_VARIANTS, notes="Same answer as olist_004."),
    dict(question="For 2018 purchases, how often did we deliver on time? Give the rate as a decimal fraction.",
         difficulty="medium", category="aggregation", uses_metric=["on_time_delivery_rate"], paraphrase_group="on_time_2018",
         gold_sql=ONTIME_2018, variants=ONTIME_2018_VARIANTS, notes="Same answer as olist_004."),
    # ---- paraphrase group C: AOV 2018 -------------------------------------------------------
    dict(question="What was average order value in 2018?", difficulty="easy", category="aggregation", uses_metric=["aov"],
         paraphrase_group="aov_2018", gold_sql=AOV_2018, variants=AOV_2018_VARIANTS,
         notes="~137 BRL, essentially flat vs 2017 AOV (~138); plausible for a marketplace with median item price ~75."),
    dict(question="What was the AOV for orders placed in 2018?", difficulty="easy", category="aggregation", uses_metric=["aov"],
         paraphrase_group="aov_2018", gold_sql=AOV_2018, variants=AOV_2018_VARIANTS, notes="Same answer as olist_007."),
    dict(question="On average, how much merchandise value did each order bring in during 2018?", difficulty="easy",
         category="aggregation", uses_metric=["aov"], paraphrase_group="aov_2018", gold_sql=AOV_2018, variants=AOV_2018_VARIANTS,
         notes="Same answer as olist_007."),
    # ---- paraphrase group D: repeat-purchase rate --------------------------------------------
    dict(question="What is our repeat-purchase rate? Return it as a fraction.", difficulty="medium", category="cohort",
         uses_metric=["repeat_purchase_rate"], paraphrase_group="repeat_rate", gold_sql=REPEAT, variants=REPEAT_VARIANTS,
         notes="~3.0%: Olist is known for very low repeat rates; customer_id-based counting gives 0 because customer_id is per order."),
    dict(question="What fraction of our customers have bought from us more than once?", difficulty="medium", category="cohort",
         uses_metric=["repeat_purchase_rate"], paraphrase_group="repeat_rate", gold_sql=REPEAT, variants=REPEAT_VARIANTS,
         notes="Same answer as olist_010."),
    dict(question="Out of everyone who has ever ordered, what share came back and placed at least a second order? (fraction)",
         difficulty="medium", category="cohort", uses_metric=["repeat_purchase_rate"], paraphrase_group="repeat_rate",
         gold_sql=REPEAT, variants=REPEAT_VARIANTS, notes="Same answer as olist_010."),
    # ---- other metric questions -------------------------------------------------------------
    dict(question="Which 5 customer states generated the most GMV in 2018? List state and GMV, highest first.",
         difficulty="medium", category="ranking", uses_metric=["gmv"],
         gold_sql=f"""SELECT c.customer_state, sum(oi.price) AS gmv
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
JOIN customers c ON c.customer_id = o.customer_id
WHERE {VALID}
  AND {Y2018}
GROUP BY c.customer_state
ORDER BY gmv DESC
LIMIT 5""",
         variants=[{"label": "gmv_including_freight", "sql": f"SELECT c.customer_state, sum(oi.price + oi.freight_value) AS gmv FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN customers c ON c.customer_id = o.customer_id WHERE {VALID} AND {Y2018} GROUP BY 1 ORDER BY 2 DESC LIMIT 5"}],
         notes="SP, RJ, MG, RS, PR: matches Brazil's population/economic ranking (SP ~40% of 2018 GMV of 7.34M)."),
    dict(question="Show monthly GMV for 2017, one row per month in chronological order.", difficulty="medium",
         category="time_series", uses_metric=["gmv"],
         gold_sql=f"""SELECT CAST(date_trunc('month', o.order_purchase_timestamp) AS DATE) AS month, sum(oi.price) AS gmv
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
WHERE {VALID}
  AND {Y2017}
GROUP BY 1
ORDER BY 1""",
         variants=[{"label": "gmv_including_freight", "sql": f"SELECT CAST(date_trunc('month', o.order_purchase_timestamp) AS DATE), sum(oi.price + oi.freight_value) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2017} GROUP BY 1 ORDER BY 1"},
                   {"label": "by_delivery_month", "sql": f"SELECT CAST(date_trunc('month', o.order_delivered_customer_date) AS DATE), sum(oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND o.order_delivered_customer_date >= TIMESTAMP '2017-01-01' AND o.order_delivered_customer_date < TIMESTAMP '2018-01-01' GROUP BY 1 ORDER BY 1"}],
         notes="12 rows; November 2017 is the peak (Black Friday 2017-11-24), consistent with the known event."),
    dict(question="How many orders did we get in 2017?", difficulty="easy", category="aggregation", uses_metric=["orders"],
         gold_sql=f"""SELECT count(DISTINCT o.order_id) AS orders
FROM orders o
WHERE {VALID}
  AND {Y2017}""",
         variants=[{"label": "including_canceled_unavailable", "sql": f"SELECT count(*) FROM orders o WHERE {Y2017}"},
                   {"label": "counting_order_items", "sql": f"SELECT count(*) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2017}"},
                   {"label": "delivered_only", "sql": f"SELECT count(*) FROM orders o WHERE o.order_status = 'delivered' AND {Y2017}"}],
         notes="~44.3k valid of 45.1k placed in 2017."),
    dict(question="How many active customers did we have in 2018?", difficulty="easy", category="aggregation",
         uses_metric=["active_customers"],
         gold_sql=f"""SELECT count(DISTINCT c.customer_unique_id) AS active_customers
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
WHERE {VALID}
  AND {Y2018}""",
         variants=[{"label": "customer_id_instead_of_unique_id", "sql": f"SELECT count(DISTINCT o.customer_id) FROM orders o WHERE {VALID} AND {Y2018}"},
                   {"label": "including_canceled_unavailable", "sql": f"SELECT count(DISTINCT c.customer_unique_id) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {Y2018}"}],
         notes="~52.3k people; slightly fewer than 2018 orders since a few people ordered twice."),
    dict(question="What is our overall cancellation rate? Return a fraction.", difficulty="easy", category="aggregation",
         uses_metric=["cancellation_rate"],
         gold_sql="""SELECT avg(CASE WHEN o.order_status = 'canceled' THEN 1.0 ELSE 0.0 END) AS cancellation_rate
FROM orders o""",
         variants=[{"label": "canceled_plus_unavailable", "sql": "SELECT avg(CASE WHEN o.order_status IN ('canceled', 'unavailable') THEN 1.0 ELSE 0.0 END) FROM orders o"}],
         notes="625 / 99,441 = 0.63%."),
    dict(question="What was the average delivery time in days for orders placed in 2017?", difficulty="medium",
         category="aggregation", uses_metric=["avg_delivery_days"],
         gold_sql=f"""SELECT avg(date_diff('second', o.order_purchase_timestamp, o.order_delivered_customer_date) / 86400.0) AS avg_delivery_days
FROM orders o
WHERE {DELIVERED}
  AND {Y2017}""",
         variants=[{"label": "median_instead_of_mean", "sql": f"SELECT median(date_diff('second', o.order_purchase_timestamp, o.order_delivered_customer_date) / 86400.0) FROM orders o WHERE {DELIVERED} AND {Y2017}"},
                   {"label": "carrier_to_customer", "sql": f"SELECT avg(date_diff('second', o.order_delivered_carrier_date, o.order_delivered_customer_date) / 86400.0) FROM orders o WHERE {DELIVERED} AND {Y2017}"},
                   {"label": "calendar_day_difference", "sql": f"SELECT avg(date_diff('day', CAST(o.order_purchase_timestamp AS DATE), CAST(o.order_delivered_customer_date AS DATE))) FROM orders o WHERE {DELIVERED} AND {Y2017}"}],
         notes="~13.0 days; long-tailed (median ~10.6)."),
    dict(question="In 2018, on average how many days early or late versus the promised date were our deliveries? Negative means early.",
         difficulty="medium", category="aggregation", uses_metric=["avg_delay_vs_promised_days"],
         gold_sql=f"""SELECT avg(date_diff('day', CAST(o.order_estimated_delivery_date AS DATE), CAST(o.order_delivered_customer_date AS DATE))) AS avg_delay_days
FROM orders o
WHERE {DELIVERED}
  AND {Y2018}""",
         variants=[{"label": "late_orders_only", "sql": f"SELECT avg(date_diff('day', CAST(o.order_estimated_delivery_date AS DATE), CAST(o.order_delivered_customer_date AS DATE))) FROM orders o WHERE {DELIVERED} AND {Y2018} AND CAST(o.order_delivered_customer_date AS DATE) > CAST(o.order_estimated_delivery_date AS DATE)"},
                   {"label": "fractional_timestamp_difference", "sql": f"SELECT avg(date_diff('second', o.order_estimated_delivery_date, o.order_delivered_customer_date) / 86400.0) FROM orders o WHERE {DELIVERED} AND {Y2018}"}],
         notes="About -11 days: Olist promises conservative dates, so most orders arrive early."),
    dict(question="What was our NPS-style review score for orders placed in 2018?", difficulty="medium", category="aggregation",
         uses_metric=["nps_style_score"],
         gold_sql=f"""SELECT 100.0 * (count(*) FILTER (WHERE r.review_score = 5) - count(*) FILTER (WHERE r.review_score <= 3)) / count(*) AS nps_style_score
FROM order_reviews r
JOIN orders o ON o.order_id = r.order_id
WHERE {Y2018}""",
         variants=[{"label": "promoters_4_and_5", "sql": f"SELECT 100.0 * (count(*) FILTER (WHERE r.review_score >= 4) - count(*) FILTER (WHERE r.review_score <= 2)) / count(*) FROM order_reviews r JOIN orders o ON o.order_id = r.order_id WHERE {Y2018}"},
                   {"label": "detractors_1_and_2_only", "sql": f"SELECT 100.0 * (count(*) FILTER (WHERE r.review_score = 5) - count(*) FILTER (WHERE r.review_score <= 2)) / count(*) FROM order_reviews r JOIN orders o ON o.order_id = r.order_id WHERE {Y2018}"}],
         notes="~35 (5-star share ~58% minus 1-3 share ~23%)."),
    dict(question="What is our freight-to-price ratio across all time?", difficulty="easy", category="aggregation",
         uses_metric=["freight_to_price_ratio"],
         gold_sql=f"""SELECT sum(oi.freight_value) / sum(oi.price) AS freight_to_price_ratio
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
WHERE {VALID}""",
         variants=[{"label": "mean_of_item_ratios", "sql": f"SELECT avg(oi.freight_value / oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID}"}],
         notes="~0.166: freight adds ~17% on top of item price."),
    dict(question="What share of GMV comes from the top 10% of sellers?", difficulty="hard", category="window",
         uses_metric=["top10pct_seller_gmv_share"],
         gold_sql=f"""WITH seller_gmv AS (
  SELECT oi.seller_id, sum(oi.price) AS gmv
  FROM order_items oi
  JOIN orders o ON o.order_id = oi.order_id
  WHERE {VALID}
  GROUP BY oi.seller_id
), ranked AS (
  SELECT gmv, row_number() OVER (ORDER BY gmv DESC) AS rk, count(*) OVER () AS n_sellers
  FROM seller_gmv
)
SELECT sum(CASE WHEN rk <= ceil(0.1 * n_sellers) THEN gmv ELSE 0 END) / sum(gmv) AS top10pct_seller_gmv_share
FROM ranked""",
         variants=[{"label": "top_10_sellers_not_10_percent", "sql": f"WITH s AS (SELECT oi.seller_id, sum(oi.price) gmv FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} GROUP BY 1) SELECT (SELECT sum(gmv) FROM (SELECT gmv FROM s ORDER BY gmv DESC LIMIT 10)) / sum(gmv) FROM s"}],
         notes="~67.5%: a heavily concentrated seller base (Pareto-like)."),
    dict(question="What is the Herfindahl-Hirschman index (0-10000 scale) of seller GMV concentration?", difficulty="hard",
         category="aggregation", uses_metric=["seller_gmv_hhi"],
         gold_sql=f"""WITH seller_gmv AS (
  SELECT oi.seller_id, sum(oi.price) AS gmv
  FROM order_items oi
  JOIN orders o ON o.order_id = oi.order_id
  WHERE {VALID}
  GROUP BY oi.seller_id
)
SELECT sum(power(100.0 * gmv / (SELECT sum(gmv) FROM seller_gmv), 2)) AS seller_gmv_hhi
FROM seller_gmv""",
         variants=[{"label": "hhi_on_order_counts", "sql": f"WITH s AS (SELECT oi.seller_id, count(DISTINCT oi.order_id) n FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} GROUP BY 1) SELECT sum(power(100.0 * n / (SELECT sum(n) FROM s), 2)) FROM s"}],
         notes="~36: unconcentrated by antitrust standards (<1500) despite top-10% share of 67%; many small sellers."),
    dict(question="Which 5 customer states had the lowest on-time delivery rate for orders placed in 2018? Show state and rate, worst first.",
         difficulty="medium", category="ranking", uses_metric=["on_time_delivery_rate"],
         gold_sql=f"""SELECT c.customer_state, avg(CASE WHEN {ON_TIME} THEN 1.0 ELSE 0.0 END) AS on_time_delivery_rate
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
WHERE {DELIVERED}
  AND {Y2018}
GROUP BY c.customer_state
ORDER BY on_time_delivery_rate ASC
LIMIT 5""",
         variants=[{"label": "timestamp_level_comparison", "sql": f"SELECT c.customer_state, avg(CASE WHEN o.order_delivered_customer_date <= o.order_estimated_delivery_date THEN 1.0 ELSE 0.0 END) r FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {DELIVERED} AND {Y2018} GROUP BY 1 ORDER BY 2 ASC LIMIT 5"}],
         notes="North/Northeast states (AL, MA, ...) worst: far from the SP seller hub, consistent with logistics geography."),
    dict(question="What were the top 5 product categories by GMV in 2018? Show category (English name) and GMV.",
         difficulty="medium", category="ranking", uses_metric=["gmv"],
         gold_sql=f"""SELECT COALESCE(t.product_category_name_english, p.product_category_name) AS product_category, sum(oi.price) AS gmv
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
LEFT JOIN products p ON p.product_id = oi.product_id
LEFT JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name
WHERE {VALID}
  AND {Y2018}
GROUP BY 1
ORDER BY gmv DESC
LIMIT 5""",
         variants=[{"label": "gmv_including_freight", "sql": f"SELECT COALESCE(t.product_category_name_english, p.product_category_name), sum(oi.price + oi.freight_value) g FROM order_items oi JOIN orders o ON o.order_id = oi.order_id LEFT JOIN products p ON p.product_id = oi.product_id LEFT JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name WHERE {VALID} AND {Y2018} GROUP BY 1 ORDER BY 2 DESC LIMIT 5"}],
         notes="health_beauty leads 2018; watches_gifts/bed_bath_table near the top, matching the well-known Olist category mix."),
    dict(question="Compare the average review score of 2018 orders delivered on time versus late. Return two rows: delivered_on_time (true/false) and avg score.",
         difficulty="hard", category="multi_join", uses_metric=["avg_review_score", "on_time_delivery_rate"],
         gold_sql=f"""SELECT {ON_TIME} AS delivered_on_time, avg(r.review_score) AS avg_review_score
FROM order_reviews r
JOIN orders o ON o.order_id = r.order_id
WHERE {DELIVERED}
  AND {Y2018}
GROUP BY 1
ORDER BY 1""",
         variants=[{"label": "timestamp_level_lateness", "sql": f"SELECT o.order_delivered_customer_date <= o.order_estimated_delivery_date, avg(r.review_score) FROM order_reviews r JOIN orders o ON o.order_id = r.order_id WHERE {DELIVERED} AND {Y2018} GROUP BY 1 ORDER BY 1"}],
         notes="Late ~2.24 vs on-time ~4.30: the well-documented lateness -> bad review effect."),
    dict(question="Which month of 2018 had the highest GMV? Return the month and its GMV.", difficulty="medium",
         category="window", uses_metric=["gmv"],
         gold_sql=f"""SELECT CAST(date_trunc('month', o.order_purchase_timestamp) AS DATE) AS month, sum(oi.price) AS gmv
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
WHERE {VALID}
  AND {Y2018}
GROUP BY 1
ORDER BY gmv DESC
LIMIT 1""",
         variants=[{"label": "gmv_including_freight", "sql": f"SELECT CAST(date_trunc('month', o.order_purchase_timestamp) AS DATE), sum(oi.price + oi.freight_value) g FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2018} GROUP BY 1 ORDER BY 2 DESC LIMIT 1"}],
         notes="One row; spring 2018 months are close to each other, so definition changes could flip the winner."),
    dict(question="Show month-over-month GMV growth for 2018 (Jan to Aug): month, GMV, and growth vs the previous month as a fraction.",
         difficulty="hard", category="window", uses_metric=["gmv"],
         gold_sql=f"""WITH monthly AS (
  SELECT CAST(date_trunc('month', o.order_purchase_timestamp) AS DATE) AS month, sum(oi.price) AS gmv
  FROM order_items oi
  JOIN orders o ON o.order_id = oi.order_id
  WHERE {VALID}
    AND o.order_purchase_timestamp >= TIMESTAMP '2017-12-01' AND o.order_purchase_timestamp < TIMESTAMP '2018-09-01'
  GROUP BY 1
)
SELECT month, gmv, gmv / lag(gmv) OVER (ORDER BY month) - 1 AS mom_growth
FROM monthly
QUALIFY month >= DATE '2018-01-01'
ORDER BY month""",
         variants=[{"label": "gmv_including_freight", "sql": f"WITH m AS (SELECT CAST(date_trunc('month', o.order_purchase_timestamp) AS DATE) AS month, sum(oi.price + oi.freight_value) gmv FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND o.order_purchase_timestamp >= TIMESTAMP '2017-12-01' AND o.order_purchase_timestamp < TIMESTAMP '2018-09-01' GROUP BY 1) SELECT month, gmv, gmv / lag(gmv) OVER (ORDER BY month) - 1 FROM m QUALIFY month >= DATE '2018-01-01' ORDER BY month"}],
         notes="8 rows; January growth is computed against December 2017 (so the window must include Dec 2017)."),
    dict(question="Which 3 customer states with at least 1,000 customers have the highest repeat-purchase rate? Show state and rate.",
         difficulty="hard", category="cohort", uses_metric=["repeat_purchase_rate"],
         gold_sql=f"""WITH per_customer AS (
  SELECT c.customer_unique_id, min(c.customer_state) AS customer_state, count(DISTINCT o.order_id) AS n_orders
  FROM orders o
  JOIN customers c ON c.customer_id = o.customer_id
  WHERE {VALID}
  GROUP BY c.customer_unique_id
)
SELECT customer_state, avg(CASE WHEN n_orders >= 2 THEN 1.0 ELSE 0.0 END) AS repeat_purchase_rate
FROM per_customer
GROUP BY customer_state
HAVING count(*) >= 1000
ORDER BY repeat_purchase_rate DESC
LIMIT 3""",
         variants=[{"label": "including_canceled_unavailable", "sql": "WITH pc AS (SELECT c.customer_unique_id, min(c.customer_state) st, count(DISTINCT o.order_id) n FROM orders o JOIN customers c ON c.customer_id = o.customer_id GROUP BY 1) SELECT st, avg(CASE WHEN n >= 2 THEN 1.0 ELSE 0.0 END) r FROM pc GROUP BY st HAVING count(*) >= 1000 ORDER BY r DESC LIMIT 3"}],
         notes="Rates around 3-4%; differences between states are small, so the ranking is sensitive."),
    dict(question="What were GMV and number of orders for customers in SP in 2018?", difficulty="easy", category="aggregation",
         uses_metric=["gmv", "orders"],
         gold_sql=f"""WITH g AS (
  SELECT sum(oi.price) AS gmv
  FROM order_items oi
  JOIN orders o ON o.order_id = oi.order_id
  JOIN customers c ON c.customer_id = o.customer_id
  WHERE {VALID} AND {Y2018} AND c.customer_state = 'SP'
), n AS (
  SELECT count(DISTINCT o.order_id) AS orders
  FROM orders o
  JOIN customers c ON c.customer_id = o.customer_id
  WHERE {VALID} AND {Y2018} AND c.customer_state = 'SP'
)
SELECT g.gmv, n.orders FROM g CROSS JOIN n""",
         variants=[{"label": "seller_state_sp_instead_of_customer", "sql": f"SELECT sum(oi.price), count(DISTINCT oi.order_id) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN sellers s ON s.seller_id = oi.seller_id WHERE {VALID} AND {Y2018} AND s.seller_state = 'SP'"}],
         notes="SP customers placed ~44% of the 53,532 valid 2018 orders."),
    # ---- non-metric questions ----------------------------------------------------------------
    dict(question="What is the status and purchase timestamp of order e481f51cbdc54678b7cc49136f2d6af7?", difficulty="easy",
         category="lookup", uses_metric=[],
         gold_sql="""SELECT order_status, order_purchase_timestamp
FROM orders
WHERE order_id = 'e481f51cbdc54678b7cc49136f2d6af7'""",
         notes="Single row: delivered, 2017-10-02 10:56:33 (first row of the raw CSV)."),
    dict(question="How many orders are currently in 'shipped' status?", difficulty="easy", category="lookup", uses_metric=[],
         gold_sql="SELECT count(*) AS n_orders FROM orders WHERE order_status = 'shipped'",
         notes="1,107, matches the status profile."),
    dict(question="Which 5 seller states have the most sellers? Show state and number of sellers.", difficulty="easy",
         category="ranking", uses_metric=[],
         gold_sql="""SELECT seller_state, count(*) AS n_sellers
FROM sellers
GROUP BY seller_state
ORDER BY n_sellers DESC
LIMIT 5""",
         notes="SP has ~60% of sellers."),
    dict(question="Which 5 product categories (English names) have the most products in the catalog? Show category and product count.",
         difficulty="easy", category="multi_join", uses_metric=[],
         gold_sql="""SELECT t.product_category_name_english AS category, count(*) AS n_products
FROM products p
JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name
GROUP BY 1
ORDER BY n_products DESC
LIMIT 5""",
         notes="bed_bath_table and sports_leisure lead the catalog."),
    dict(question="How many payment records are there for each payment type? Order by count descending.", difficulty="easy",
         category="aggregation", uses_metric=[],
         gold_sql="""SELECT payment_type, count(*) AS n_payments
FROM order_payments
GROUP BY payment_type
ORDER BY n_payments DESC""",
         notes="credit_card 76,795; boleto 19,784; voucher; debit_card; not_defined 3."),
    dict(question="What is the average number of installments for credit card payments?", difficulty="easy", category="aggregation",
         uses_metric=[],
         gold_sql="SELECT avg(payment_installments) AS avg_installments FROM order_payments WHERE payment_type = 'credit_card'",
         notes="~3.5 installments; Brazilian card installments are common."),
    dict(question="Which 5 sellers appear in the most distinct orders (any order status)? Show seller_id and number of orders.",
         difficulty="medium", category="ranking", uses_metric=[],
         gold_sql="""SELECT seller_id, count(DISTINCT order_id) AS n_orders
FROM order_items
GROUP BY seller_id
ORDER BY n_orders DESC, seller_id
LIMIT 5""",
         notes="Top seller ~1.85k orders; long tail of small sellers."),
    dict(question="How many distinct people (customer_unique_id) have ever placed an order of any status, and how many customer_id values are there?",
         difficulty="easy", category="lookup", uses_metric=[],
         gold_sql="""SELECT count(DISTINCT c.customer_unique_id) AS people, count(DISTINCT o.customer_id) AS customer_ids
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id""",
         notes="96,096 people vs 99,441 customer_ids."),
    dict(question="For each month of 2017, how many orders were placed (any status) and what was the cumulative total so far that year?",
         difficulty="medium", category="window", uses_metric=[],
         gold_sql=f"""SELECT CAST(date_trunc('month', o.order_purchase_timestamp) AS DATE) AS month,
       count(*) AS n_orders,
       sum(count(*)) OVER (ORDER BY CAST(date_trunc('month', o.order_purchase_timestamp) AS DATE)) AS cumulative_orders
FROM orders o
WHERE {Y2017}
GROUP BY 1
ORDER BY 1""",
         notes="12 rows; cumulative ends at 45,101 (all 2017 orders)."),
    dict(question="How many new customers (by first-ever purchase, any order status, counted by customer_unique_id) did we acquire each month in 2017?",
         difficulty="medium", category="cohort", uses_metric=[],
         gold_sql="""WITH first_purchase AS (
  SELECT c.customer_unique_id, min(o.order_purchase_timestamp) AS first_ts
  FROM orders o
  JOIN customers c ON c.customer_id = o.customer_id
  GROUP BY c.customer_unique_id
)
SELECT CAST(date_trunc('month', first_ts) AS DATE) AS month, count(*) AS new_customers
FROM first_purchase
WHERE first_ts >= TIMESTAMP '2017-01-01' AND first_ts < TIMESTAMP '2018-01-01'
GROUP BY 1
ORDER BY 1""",
         notes="Tracks monthly order counts closely because repeat buying is rare."),
    dict(question="For customers whose first order (any status) was in January 2017, what fraction placed another order within 180 days of that first order?",
         difficulty="hard", category="cohort", uses_metric=[],
         gold_sql="""WITH person_orders AS (
  SELECT c.customer_unique_id, o.order_id, o.order_purchase_timestamp AS ts
  FROM orders o
  JOIN customers c ON c.customer_id = o.customer_id
), first_purchase AS (
  SELECT customer_unique_id, min(ts) AS first_ts
  FROM person_orders
  GROUP BY customer_unique_id
), cohort AS (
  SELECT * FROM first_purchase
  WHERE first_ts >= TIMESTAMP '2017-01-01' AND first_ts < TIMESTAMP '2017-02-01'
)
SELECT avg(CASE WHEN EXISTS (
         SELECT 1 FROM person_orders p
         WHERE p.customer_unique_id = cohort.customer_unique_id
           AND p.ts > cohort.first_ts
           AND p.ts <= cohort.first_ts + INTERVAL 180 DAY) THEN 1.0 ELSE 0.0 END) AS retained_180d
FROM cohort""",
         notes="~3.9% retained within 180 days: very low retention, consistent with the ~3% lifetime repeat rate."),
    dict(question="What is the average freight per item when the seller and customer are in the same state versus different states? Two rows.",
         difficulty="medium", category="multi_join", uses_metric=[],
         gold_sql="""SELECT s.seller_state = c.customer_state AS same_state, avg(oi.freight_value) AS avg_freight
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
JOIN customers c ON c.customer_id = o.customer_id
JOIN sellers s ON s.seller_id = oi.seller_id
GROUP BY 1
ORDER BY 1""",
         notes="Interstate freight is clearly higher (~23.7 vs ~13.5 BRL), as expected."),
    dict(question="Which 3 product categories (English) have the heaviest products on average? Show category and average weight in grams.",
         difficulty="easy", category="multi_join", uses_metric=[],
         gold_sql="""SELECT t.product_category_name_english AS category, avg(p.product_weight_g) AS avg_weight_g
FROM products p
JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name
GROUP BY 1
ORDER BY avg_weight_g DESC
LIMIT 3""",
         notes="Furniture/office furniture categories, as expected."),
    dict(question="How many orders contain items from two or more different sellers?", difficulty="medium", category="aggregation",
         uses_metric=[],
         gold_sql="""SELECT count(*) AS multi_seller_orders
FROM (
  SELECT order_id
  FROM order_items
  GROUP BY order_id
  HAVING count(DISTINCT seller_id) >= 2
) t""",
         notes="~1.3k orders, about 1% of orders with items."),
    dict(question="Which day of the week gets the most orders (any status)? Return the weekday name and number of orders, for all days, most first.",
         difficulty="easy", category="time_series", uses_metric=[],
         gold_sql="""SELECT dayname(order_purchase_timestamp) AS weekday, count(*) AS n_orders
FROM orders
GROUP BY 1
ORDER BY n_orders DESC""",
         notes="Monday is the busiest day, weekends lowest, typical for e-commerce in Brazil."),
    dict(question="What is the average number of items per order, over orders that have at least one item?", difficulty="easy",
         category="aggregation", uses_metric=[],
         gold_sql="""SELECT count(*) * 1.0 / count(DISTINCT order_id) AS avg_items_per_order
FROM order_items""",
         notes="~1.14 items per order."),
    dict(question="For each of the 3 customer states with the most orders, which product category (English) sold the most items there and how many? Any order status.",
         difficulty="hard", category="window", uses_metric=[],
         gold_sql="""WITH top_states AS (
  SELECT c.customer_state
  FROM orders o JOIN customers c ON c.customer_id = o.customer_id
  GROUP BY 1
  ORDER BY count(*) DESC
  LIMIT 3
), cat_counts AS (
  SELECT c.customer_state, t.product_category_name_english AS category, count(*) AS n_items
  FROM order_items oi
  JOIN orders o ON o.order_id = oi.order_id
  JOIN customers c ON c.customer_id = o.customer_id
  JOIN products p ON p.product_id = oi.product_id
  JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name
  WHERE c.customer_state IN (SELECT customer_state FROM top_states)
  GROUP BY 1, 2
)
SELECT customer_state, category, n_items
FROM cat_counts
QUALIFY row_number() OVER (PARTITION BY customer_state ORDER BY n_items DESC) = 1
ORDER BY customer_state""",
         notes="SP/RJ/MG; bed_bath_table dominates by item count in the big states."),
    dict(question="Which 5 sellers had the most late deliveries (delivered after the estimated date) on delivered orders? Show seller_id and number of late orders.",
         difficulty="medium", category="ranking", uses_metric=[],
         gold_sql=f"""SELECT oi.seller_id, count(DISTINCT o.order_id) AS late_orders
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
WHERE {DELIVERED}
  AND CAST(o.order_delivered_customer_date AS DATE) > CAST(o.order_estimated_delivery_date AS DATE)
GROUP BY oi.seller_id
ORDER BY late_orders DESC, oi.seller_id
LIMIT 5""",
         notes="Large sellers dominate in absolute counts, as expected."),
    dict(question="What is the busiest hour of the day for placing orders? Return the hour (0-23) and its number of orders.",
         difficulty="easy", category="time_series", uses_metric=[],
         gold_sql="""SELECT hour(order_purchase_timestamp) AS hour, count(*) AS n_orders
FROM orders
GROUP BY 1
ORDER BY n_orders DESC
LIMIT 1""",
         notes="Peak at 16h (6,675 orders), with 11h and 14h close behind."),
    dict(question="What share of orders with any review received a 1-star review? Count each order once (an order is 1-star if any of its reviews is 1).",
         difficulty="medium", category="aggregation", uses_metric=[],
         gold_sql="""WITH per_order AS (
  SELECT order_id, min(review_score) AS min_score
  FROM order_reviews
  GROUP BY order_id
)
SELECT avg(CASE WHEN min_score = 1 THEN 1.0 ELSE 0.0 END) AS one_star_share
FROM per_order""",
         notes="~11-12% of reviewed orders."),
]

# ---------------------------------------------------------------------------------------------
# Ambiguous questions: each needs >= 2 materially different executed interpretations.
# resolved_by_governed_layer = the governed metric layer defines the answer (so asking is optional
# when the agent has the semantic layer; it is still ambiguous for a raw-SQL agent).
# ---------------------------------------------------------------------------------------------
Q2_2018 = "o.order_purchase_timestamp >= TIMESTAMP '2018-04-01' AND o.order_purchase_timestamp < TIMESTAMP '2018-07-01'"
Q3_2018 = "o.order_purchase_timestamp >= TIMESTAMP '2018-07-01' AND o.order_purchase_timestamp < TIMESTAMP '2018-10-01'"
NORTH = "('AM', 'PA', 'RR', 'AP', 'AC', 'RO', 'TO')"
NORTH_NORTHEAST = "('AM', 'PA', 'RR', 'AP', 'AC', 'RO', 'TO', 'MA', 'PI', 'CE', 'RN', 'PB', 'PE', 'AL', 'SE', 'BA')"
REGION_CASE = """CASE WHEN c.customer_state IN ('AM','PA','RR','AP','AC','RO','TO') THEN 'North'
            WHEN c.customer_state IN ('MA','PI','CE','RN','PB','PE','AL','SE','BA') THEN 'Northeast'
            WHEN c.customer_state IN ('MT','MS','GO','DF') THEN 'Center-West'
            WHEN c.customer_state IN ('SP','RJ','MG','ES') THEN 'Southeast'
            ELSE 'South' END"""

AMBIGUOUS: list[dict] = [
    dict(question="What was revenue last quarter?", ambiguity_type="time_window", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "Q3 2018 (latest calendar quarter, partial), GMV", "sql": f"SELECT sum(oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Q3_2018}"},
             {"label": "Q2 2018 (last complete quarter), GMV", "sql": f"SELECT sum(oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Q2_2018}"},
             {"label": "Q2 2018, items + freight", "sql": f"SELECT sum(oi.price + oi.freight_value) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Q2_2018}"}]),
    dict(question="Who are our best customers?", ambiguity_type="ranking_criterion", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "top 5 people by lifetime GMV", "sql": f"SELECT c.customer_unique_id, sum(oi.price) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN customers c ON c.customer_id = o.customer_id WHERE {VALID} GROUP BY 1 ORDER BY v DESC, 1 LIMIT 5"},
             {"label": "top 5 people by number of orders", "sql": f"SELECT c.customer_unique_id, count(DISTINCT o.order_id) v FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {VALID} GROUP BY 1 ORDER BY v DESC, 1 LIMIT 5"}]),
    dict(question="How fast is our delivery?", ambiguity_type="aggregation", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "mean purchase-to-delivery days", "sql": f"SELECT avg(date_diff('second', o.order_purchase_timestamp, o.order_delivered_customer_date) / 86400.0) FROM orders o WHERE {DELIVERED}"},
             {"label": "median purchase-to-delivery days", "sql": f"SELECT median(date_diff('second', o.order_purchase_timestamp, o.order_delivered_customer_date) / 86400.0) FROM orders o WHERE {DELIVERED}"},
             {"label": "mean carrier-to-customer days", "sql": f"SELECT avg(date_diff('second', o.order_delivered_carrier_date, o.order_delivered_customer_date) / 86400.0) FROM orders o WHERE {DELIVERED}"}]),
    dict(question="What's our top category?", ambiguity_type="ranking_criterion", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "by GMV", "sql": f"SELECT t.product_category_name_english, sum(oi.price) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name WHERE {VALID} GROUP BY 1 ORDER BY v DESC LIMIT 1"},
             {"label": "by items sold", "sql": f"SELECT t.product_category_name_english, count(*) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name WHERE {VALID} GROUP BY 1 ORDER BY v DESC LIMIT 1"}]),
    dict(question="How did we do last month?", ambiguity_type="time_window", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "October 2018 (latest month with any data)", "sql": "SELECT count(DISTINCT o.order_id), sum(oi.price) FROM orders o LEFT JOIN order_items oi ON oi.order_id = o.order_id WHERE o.order_purchase_timestamp >= TIMESTAMP '2018-10-01' AND o.order_purchase_timestamp < TIMESTAMP '2018-11-01'"},
             {"label": "August 2018 (last complete month)", "sql": f"SELECT count(DISTINCT o.order_id), sum(oi.price) FROM orders o JOIN order_items oi ON oi.order_id = o.order_id WHERE {VALID} AND o.order_purchase_timestamp >= TIMESTAMP '2018-08-01' AND o.order_purchase_timestamp < TIMESTAMP '2018-09-01'"}]),
    dict(question="How many sellers do we have in Sao Paulo?", ambiguity_type="entity", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "Sao Paulo city", "sql": "SELECT count(*) FROM sellers WHERE seller_city = 'sao paulo'"},
             {"label": "Sao Paulo state (SP)", "sql": "SELECT count(*) FROM sellers WHERE seller_state = 'SP'"}]),
    dict(question="How many orders did we have in Rio?", ambiguity_type="entity", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "Rio de Janeiro city", "sql": "SELECT count(*) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE c.customer_city = 'rio de janeiro'"},
             {"label": "Rio de Janeiro state (RJ)", "sql": "SELECT count(*) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE c.customer_state = 'RJ'"},
             {"label": "all cities starting with 'rio'", "sql": "SELECT count(*) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE c.customer_city LIKE 'rio%'"}]),
    dict(question="How many late orders did we have in 2018?", ambiguity_type="metric_definition", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "delivered after the estimated date (date level)", "sql": f"SELECT count(*) FROM orders o WHERE {DELIVERED} AND {Y2018} AND CAST(o.order_delivered_customer_date AS DATE) > CAST(o.order_estimated_delivery_date AS DATE)"},
             {"label": "delivered after the estimated timestamp (midnight)", "sql": f"SELECT count(*) FROM orders o WHERE {DELIVERED} AND {Y2018} AND o.order_delivered_customer_date > o.order_estimated_delivery_date"},
             {"label": "date-level late plus never delivered past the estimate", "sql": f"SELECT count(*) FROM orders o WHERE {Y2018} AND ((o.order_delivered_customer_date IS NOT NULL AND CAST(o.order_delivered_customer_date AS DATE) > CAST(o.order_estimated_delivery_date AS DATE)) OR (o.order_delivered_customer_date IS NULL AND o.order_estimated_delivery_date < TIMESTAMP '2018-10-17'))"}]),
    dict(question="Which product category grew the most?", ambiguity_type="ranking_criterion", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "largest absolute GMV increase, Jan-Aug 2018 vs Jan-Aug 2017", "sql": f"WITH x AS (SELECT t.product_category_name_english cat, sum(oi.price) FILTER (WHERE o.order_purchase_timestamp >= TIMESTAMP '2018-01-01' AND o.order_purchase_timestamp < TIMESTAMP '2018-09-01') g18, sum(oi.price) FILTER (WHERE o.order_purchase_timestamp >= TIMESTAMP '2017-01-01' AND o.order_purchase_timestamp < TIMESTAMP '2017-09-01') g17 FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name WHERE {VALID} GROUP BY 1) SELECT cat, g18 - g17 FROM x ORDER BY 2 DESC NULLS LAST LIMIT 1"},
             {"label": "largest relative GMV growth (min 10k BRL in 2017 Jan-Aug)", "sql": f"WITH x AS (SELECT t.product_category_name_english cat, sum(oi.price) FILTER (WHERE o.order_purchase_timestamp >= TIMESTAMP '2018-01-01' AND o.order_purchase_timestamp < TIMESTAMP '2018-09-01') g18, sum(oi.price) FILTER (WHERE o.order_purchase_timestamp >= TIMESTAMP '2017-01-01' AND o.order_purchase_timestamp < TIMESTAMP '2017-09-01') g17 FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name WHERE {VALID} GROUP BY 1) SELECT cat, g18 / g17 - 1 FROM x WHERE g17 >= 10000 ORDER BY 2 DESC NULLS LAST LIMIT 1"}]),
    dict(question="What's our customer retention rate?", ambiguity_type="metric_definition", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "lifetime share of people with 2+ valid orders", "sql": f"WITH pc AS (SELECT c.customer_unique_id, count(DISTINCT o.order_id) n FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {VALID} GROUP BY 1) SELECT avg(CASE WHEN n >= 2 THEN 1.0 ELSE 0.0 END) FROM pc"},
             {"label": "share of 2017-active people who ordered again in 2018", "sql": f"WITH a AS (SELECT DISTINCT c.customer_unique_id FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {Y2017}), b AS (SELECT DISTINCT c.customer_unique_id FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {Y2018}) SELECT avg(CASE WHEN b.customer_unique_id IS NULL THEN 0.0 ELSE 1.0 END) FROM a LEFT JOIN b USING (customer_unique_id)"},
             {"label": "share with a second order within 90 days of the first (first order before 2018-07)", "sql": "WITH po AS (SELECT c.customer_unique_id u, o.order_purchase_timestamp ts FROM orders o JOIN customers c ON c.customer_id = o.customer_id), f AS (SELECT u, min(ts) f FROM po GROUP BY 1 HAVING min(ts) < TIMESTAMP '2018-07-01') SELECT avg(CASE WHEN EXISTS (SELECT 1 FROM po WHERE po.u = f.u AND po.ts > f.f AND po.ts <= f.f + INTERVAL 90 DAY) THEN 1.0 ELSE 0.0 END) FROM f"}]),
    dict(question="Show me sales by region.", ambiguity_type="entity", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "GMV by customer state (27 rows)", "sql": f"SELECT c.customer_state, sum(oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN customers c ON c.customer_id = o.customer_id WHERE {VALID} GROUP BY 1 ORDER BY 2 DESC"},
             {"label": "GMV by IBGE macro-region of the customer (5 rows)", "sql": f"SELECT {REGION_CASE} AS region, sum(oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN customers c ON c.customer_id = o.customer_id WHERE {VALID} GROUP BY 1 ORDER BY 2 DESC"},
             {"label": "GMV by seller state", "sql": f"SELECT s.seller_state, sum(oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN sellers s ON s.seller_id = oi.seller_id WHERE {VALID} GROUP BY 1 ORDER BY 2 DESC"}]),
    dict(question="What's the average delivery time in Sao Paulo?", ambiguity_type="entity", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "Sao Paulo city", "sql": f"SELECT avg(date_diff('second', o.order_purchase_timestamp, o.order_delivered_customer_date) / 86400.0) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {DELIVERED} AND c.customer_city = 'sao paulo'"},
             {"label": "Sao Paulo state (SP)", "sql": f"SELECT avg(date_diff('second', o.order_purchase_timestamp, o.order_delivered_customer_date) / 86400.0) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {DELIVERED} AND c.customer_state = 'SP'"}]),
    dict(question="How much do customers typically spend?", ambiguity_type="aggregation", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "mean order value (AOV)", "sql": f"SELECT sum(oi.price) / count(DISTINCT oi.order_id) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID}"},
             {"label": "median order value", "sql": f"SELECT median(v) FROM (SELECT oi.order_id, sum(oi.price) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} GROUP BY 1)"},
             {"label": "mean lifetime spend per person", "sql": f"SELECT avg(v) FROM (SELECT c.customer_unique_id, sum(oi.price) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN customers c ON c.customer_id = o.customer_id WHERE {VALID} GROUP BY 1)"}]),
    dict(question="Who are our top sellers this year?", ambiguity_type="time_window", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "2018 (latest year in the data), top 5 by GMV", "sql": f"SELECT oi.seller_id, sum(oi.price) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2018} GROUP BY 1 ORDER BY v DESC LIMIT 5"},
             {"label": "2018, top 5 by number of orders", "sql": f"SELECT oi.seller_id, count(DISTINCT oi.order_id) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2018} GROUP BY 1 ORDER BY v DESC, 1 LIMIT 5"},
             {"label": "calendar 2026 (today): no data", "sql": "SELECT count(*) FROM orders o WHERE o.order_purchase_timestamp >= TIMESTAMP '2026-01-01'"}]),
    dict(question="How many items did we sell last year?", ambiguity_type="time_window", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "2017 (last complete year in the data)", "sql": f"SELECT count(*) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2017}"},
             {"label": "2018 (latest year in the data, Jan-Oct)", "sql": f"SELECT count(*) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2018}"},
             {"label": "2025 relative to today (2026): no data", "sql": "SELECT count(*) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE o.order_purchase_timestamp >= TIMESTAMP '2025-01-01' AND o.order_purchase_timestamp < TIMESTAMP '2026-01-01'"}]),
    dict(question="Which sellers are underperforming?", ambiguity_type="ranking_criterion", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "lowest avg review score (min 50 reviewed orders), bottom 5", "sql": "SELECT oi.seller_id, avg(r.review_score) v FROM order_items oi JOIN order_reviews r ON r.order_id = oi.order_id GROUP BY 1 HAVING count(DISTINCT oi.order_id) >= 50 ORDER BY v, 1 LIMIT 5"},
             {"label": "highest late-delivery share (min 50 delivered orders), worst 5", "sql": f"SELECT oi.seller_id, avg(CASE WHEN {ON_TIME} THEN 0.0 ELSE 1.0 END) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {DELIVERED} GROUP BY 1 HAVING count(DISTINCT o.order_id) >= 50 ORDER BY v DESC, 1 LIMIT 5"},
             {"label": "highest cancellation share (min 50 orders), worst 5", "sql": "SELECT oi.seller_id, avg(CASE WHEN o.order_status = 'canceled' THEN 1.0 ELSE 0.0 END) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id GROUP BY 1 HAVING count(DISTINCT o.order_id) >= 50 ORDER BY v DESC, 1 LIMIT 5"}]),
    dict(question="How many customers do we have?", ambiguity_type="entity", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "customer_id rows", "sql": "SELECT count(*) FROM customers"},
             {"label": "distinct people with a delivered order", "sql": "SELECT count(DISTINCT c.customer_unique_id) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE o.order_status = 'delivered'"},
             {"label": "people active in 2018 (valid orders)", "sql": f"SELECT count(DISTINCT c.customer_unique_id) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {VALID} AND {Y2018}"}]),
    dict(question="What's our average order value?", ambiguity_type="metric_definition", resolved_by_governed_layer=True,
         interpretations=[
             {"label": "governed AOV: item price only, valid orders", "sql": f"SELECT sum(oi.price) / count(DISTINCT oi.order_id) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID}"},
             {"label": "items + freight per order", "sql": f"SELECT sum(oi.price + oi.freight_value) / count(DISTINCT oi.order_id) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID}"}]),
    dict(question="What's the cancellation rate?", ambiguity_type="metric_definition", resolved_by_governed_layer=True,
         interpretations=[
             {"label": "governed: canceled / all orders", "sql": "SELECT avg(CASE WHEN order_status = 'canceled' THEN 1.0 ELSE 0.0 END) FROM orders"},
             {"label": "canceled + unavailable / all orders", "sql": "SELECT avg(CASE WHEN order_status IN ('canceled', 'unavailable') THEN 1.0 ELSE 0.0 END) FROM orders"}]),
    dict(question="How big is our business?", ambiguity_type="metric_definition", resolved_by_governed_layer=False,
         interpretations=[
             {"label": "lifetime GMV (BRL)", "sql": f"SELECT sum(oi.price) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID}"},
             {"label": "lifetime valid orders", "sql": f"SELECT count(*) FROM orders o WHERE {VALID}"},
             {"label": "number of sellers", "sql": "SELECT count(*) FROM sellers"}]),
]

CONTROLS: list[dict] = [
    dict(question="What was GMV (sum of item prices, excluding freight, excluding canceled and unavailable orders) for orders placed in 2017?", gold_sql=GMV_2017),
    dict(question="How many orders have order_status 'delivered'?", gold_sql="SELECT count(*) FROM orders WHERE order_status = 'delivered'"),
    dict(question="What is the mean number of days from purchase to customer delivery for delivered orders placed in 2017?", gold_sql=f"SELECT avg(date_diff('second', o.order_purchase_timestamp, o.order_delivered_customer_date) / 86400.0) FROM orders o WHERE {DELIVERED} AND {Y2017}"),
    dict(question="How many orders were placed by customers whose customer_state is 'RJ'?", gold_sql="SELECT count(*) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE c.customer_state = 'RJ'"),
    dict(question="How many orders were placed by customers in the city of 'rio de janeiro'?", gold_sql="SELECT count(*) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE c.customer_city = 'rio de janeiro'"),
    dict(question="Which product category (English name) has the most items sold across non-canceled, available orders? Return the category and item count.", gold_sql=f"SELECT t.product_category_name_english, count(*) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id JOIN product_category_name_translation t ON t.product_category_name = p.product_category_name WHERE {VALID} GROUP BY 1 ORDER BY v DESC LIMIT 1"),
    dict(question="How many distinct customer_unique_id values are in the customers table?", gold_sql="SELECT count(DISTINCT customer_unique_id) FROM customers"),
    dict(question="What fraction of all orders (any status) have order_status 'canceled'?", gold_sql="SELECT avg(CASE WHEN order_status = 'canceled' THEN 1.0 ELSE 0.0 END) FROM orders"),
    dict(question="List the 5 sellers with the highest total item price on valid (not canceled/unavailable) orders placed in 2018, with that total, highest first.", gold_sql=f"SELECT oi.seller_id, sum(oi.price) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} AND {Y2018} GROUP BY 1 ORDER BY v DESC LIMIT 5"),
    dict(question="What is the median of the total item price per order, over valid orders that have items?", gold_sql=f"SELECT median(v) FROM (SELECT oi.order_id, sum(oi.price) v FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {VALID} GROUP BY 1)"),
    dict(question="How many orders were placed in August 2018 (any status)?", gold_sql="SELECT count(*) FROM orders WHERE order_purchase_timestamp >= TIMESTAMP '2018-08-01' AND order_purchase_timestamp < TIMESTAMP '2018-09-01'"),
    dict(question="What is the total payment_value across all payment records with payment_type 'credit_card'?", gold_sql="SELECT sum(payment_value) FROM order_payments WHERE payment_type = 'credit_card'"),
    dict(question="How many sellers are located in seller_state 'SP'?", gold_sql="SELECT count(*) FROM sellers WHERE seller_state = 'SP'"),
    dict(question="What is the average review_score over all rows of the order_reviews table?", gold_sql="SELECT avg(review_score) FROM order_reviews"),
    dict(question="For delivered orders, what fraction arrived on or before the estimated delivery date (comparing dates, not times)?", gold_sql=f"SELECT avg(CASE WHEN {ON_TIME} THEN 1.0 ELSE 0.0 END) FROM orders o WHERE {DELIVERED}"),
    dict(question="What is the average freight_value per order item?", gold_sql="SELECT avg(freight_value) FROM order_items"),
    dict(question="How many products have no product_category_name?", gold_sql="SELECT count(*) FROM products WHERE product_category_name IS NULL"),
    dict(question="How many order reviews have a review_score of 1?", gold_sql="SELECT count(*) FROM order_reviews WHERE review_score = 1"),
    dict(question="What is the maximum number of items (order_item_id) in a single order?", gold_sql="SELECT max(order_item_id) FROM order_items"),
    dict(question="How many customers (distinct customer_unique_id) placed at least one valid (not canceled or unavailable) order in 2018?", gold_sql=f"SELECT count(DISTINCT c.customer_unique_id) FROM orders o JOIN customers c ON c.customer_id = o.customer_id WHERE {VALID} AND {Y2018}"),
]


# ---------------------------------------------------------------------------------------------
def jsonable(v):
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, float):
        return round(v, 6)
    return v


def rows_of(con, sql):
    return con.execute(sql).fetchall()


def first_number(rows):
    for row in rows:
        for v in row:
            if isinstance(v, int | float | decimal.Decimal) and not isinstance(v, bool):
                return float(v)
    return None


def rel_diff(gold_rows, var_rows) -> float | str:
    """Relative difference on the first numeric value; 'different_rows' if keys/shape differ."""
    if gold_rows == var_rows:
        return 0.0
    g, v = first_number(gold_rows), first_number(var_rows)
    if g is not None and v is not None and len(gold_rows) == 1 and len(var_rows) == 1:
        return round(abs(v - g) / abs(g), 6) if g else float("inf")
    # multi-row: compare numeric columns row-wise; fall back to a label
    if len(gold_rows) == len(var_rows):
        diffs = []
        for gr, vr in zip(gold_rows, var_rows, strict=True):
            for a, b in zip(gr, vr, strict=False):
                if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool) and a:
                    diffs.append(abs(b - a) / abs(a))
                elif a != b:
                    return "different_rows"
        return round(max(diffs), 6) if diffs else "different_rows"
    return "different_rows"


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    OUT.mkdir(exist_ok=True)
    failures = 0

    golden_out = []
    for i, q in enumerate(GOLDEN, start=1):
        rec = {"id": f"olist_{i:03d}", **{k: q.get(k) for k in ("question", "difficulty", "category", "uses_metric")},
               "paraphrase_group": q.get("paraphrase_group"), "gold_sql": q["gold_sql"]}
        rows = rows_of(con, q["gold_sql"])
        rec["answer_preview"] = [[jsonable(v) for v in r] for r in rows[:3]]
        rec["n_rows"] = len(rows)
        rec["notes"] = q.get("notes", "")
        if not rows:
            print(f"EMPTY gold result: {rec['id']}")
            failures += 1
        if q.get("variants"):
            kept = []
            for var in q["variants"]:
                vrows = rows_of(con, var["sql"])
                d = rel_diff(rows, vrows)
                if d == 0.0:
                    print(f"  dropped variant {var['label']} of {rec['id']}: same answer as gold")
                    continue
                kept.append({**var, "rel_diff": d, "answer_preview": [[jsonable(v) for v in r] for r in vrows[:3]]})
            rec["variants"] = kept
        else:
            rec["variants"] = []
        golden_out.append(rec)

    amb_out = []
    for i, q in enumerate(AMBIGUOUS, start=1):
        interps = []
        for it in q["interpretations"]:
            r = rows_of(con, it["sql"])
            interps.append({**it, "answer_preview": [[jsonable(v) for v in row] for row in r[:5]], "n_rows": len(r)})
        # materiality: any pair differs by > 5% on the first number, or shows different keys / shape
        base = rows_of(con, q["interpretations"][0]["sql"])
        diffs = []
        for it in q["interpretations"][1:]:
            diffs.append(rel_diff(base, rows_of(con, it["sql"])))
        material = any(d == "different_rows" or (isinstance(d, float) and d > 0.05) for d in diffs)
        if not material:
            print(f"NOT MATERIAL: ambiguous {i}: {q['question']} diffs={diffs}")
            failures += 1
        amb_out.append({"id": f"amb_{i:03d}", "question": q["question"], "ambiguity_type": q["ambiguity_type"],
                        "resolved_by_governed_layer": q["resolved_by_governed_layer"], "interpretations": interps,
                        "material_difference": {"rel_diff_vs_first": diffs,
                                                "values": [it["answer_preview"][:2] for it in interps]}})

    ctl_out = []
    for i, q in enumerate(CONTROLS, start=1):
        r = rows_of(con, q["gold_sql"])
        if not r:
            failures += 1
        ctl_out.append({"id": f"ctl_{i:03d}", "question": q["question"], "gold_sql": q["gold_sql"],
                        "answer_preview": [[jsonable(v) for v in row] for row in r[:3]], "n_rows": len(r)})

    for name, recs in [("olist_golden", golden_out), ("olist_ambiguous", amb_out), ("olist_clear_controls", ctl_out)]:
        with open(OUT / f"{name}.jsonl", "w") as f:
            for rec in recs:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        print(f"wrote {name}.jsonl: {len(recs)} records")
    if failures:
        sys.exit(f"{failures} problems")


if __name__ == "__main__":
    main()
