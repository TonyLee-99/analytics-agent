"""Load the raw Olist CSVs into a DuckDB 'warehouse' with a governed access layer.

Layout
- schema `raw`  : the 9 source tables exactly as published (never exposed to the agent).
- schema `main` : governed views the agent may query. PII columns are tokenized/coarsened here
                  (see PII_POLICY) so the policy holds no matter what SQL the LLM writes.

Fails loudly if any row count differs from the published dataset.
Run: uv run python scripts/build_olist.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.config import OLIST_DB, OLIST_RAW  # noqa: E402

TABLES = {
    # table name: (csv file, expected rows)
    "customers": ("olist_customers_dataset.csv", 99441),
    "geolocation": ("olist_geolocation_dataset.csv", 1000163),
    "order_items": ("olist_order_items_dataset.csv", 112650),
    "order_payments": ("olist_order_payments_dataset.csv", 103886),
    "order_reviews": ("olist_order_reviews_dataset.csv", 99224),
    "orders": ("olist_orders_dataset.csv", 99441),
    "products": ("olist_products_dataset.csv", 32951),
    "sellers": ("olist_sellers_dataset.csv", 3095),
    "product_category_name_translation": ("product_category_name_translation.csv", 71),
}

# zip-code prefixes are quasi-identifiers: tokenize deterministically (joins still work, values hidden);
# lat/lng are coarsened to 1 decimal (~11 km).
TOKEN = "'zip_' || substr(md5({c}), 1, 10)"
PII_POLICY = {
    "customers": {"customer_zip_code_prefix": TOKEN.format(c="customer_zip_code_prefix")},
    "sellers": {"seller_zip_code_prefix": TOKEN.format(c="seller_zip_code_prefix")},
    "geolocation": {
        "geolocation_zip_code_prefix": TOKEN.format(c="geolocation_zip_code_prefix"),
        "geolocation_lat": "round(geolocation_lat, 1)",
        "geolocation_lng": "round(geolocation_lng, 1)",
    },
}

TYPES = {
    "customers": {"customer_zip_code_prefix": "VARCHAR"},
    "sellers": {"seller_zip_code_prefix": "VARCHAR"},
    "geolocation": {"geolocation_zip_code_prefix": "VARCHAR"},
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    if OLIST_DB.exists():
        OLIST_DB.unlink()
    con = duckdb.connect(str(OLIST_DB))
    con.execute("CREATE SCHEMA raw")
    for table, (fname, expected) in TABLES.items():
        path = OLIST_RAW / fname
        types = TYPES.get(table, {})
        type_arg = ", types={" + ", ".join(f"'{k}': '{v}'" for k, v in types.items()) + "}" if types else ""
        con.execute(
            f"CREATE TABLE raw.{table} AS SELECT * FROM read_csv('{path}', header=true, auto_detect=true{type_arg})"
        )
        n = con.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]
        status = "OK" if n == expected else "MISMATCH"
        print(f"{table:38s} rows={n:>9,d} expected={expected:>9,d} {status}  sha256={sha256(path)[:12]}")
        if n != expected:
            raise SystemExit(f"row count mismatch for {table}")

    # governed views in `main`
    for table in TABLES:
        cols = [r[0] for r in con.execute(f"DESCRIBE raw.{table}").fetchall()]
        policy = PII_POLICY.get(table, {})
        select = ", ".join(f"{policy[c]} AS {c}" if c in policy else c for c in cols)
        con.execute(f"CREATE VIEW main.{table} AS SELECT {select} FROM raw.{table}")
    con.execute("CHECKPOINT")
    con.close()
    print(f"wrote {OLIST_DB} ({OLIST_DB.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
