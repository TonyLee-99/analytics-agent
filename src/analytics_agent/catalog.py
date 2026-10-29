"""Data catalog: tables, columns, keys, human descriptions and sample values for one database.

This is the "metadata" half of schema RAG. In a big company this comes from the data catalog
(DataHub / Amundsen / Unity Catalog); here we build it from
- BIRD: sqlite_master + PRAGMA + the benchmark's `database_description/*.csv` column docs
- Olist: DuckDB information_schema + our hand-written catalog_docs/olist.yaml
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import duckdb
import yaml

from analytics_agent.config import BIRD_DB_ROOT, CATALOG_DOCS, DATA, OLIST_DB, REDTEAM_DB

CACHE = DATA / "catalog_cache"


@dataclass
class Column:
    name: str
    type: str
    description: str = ""
    value_description: str = ""
    samples: list[str] = field(default_factory=list)
    pk: bool = False
    pii: bool = False


@dataclass
class Table:
    name: str
    columns: list[Column]
    description: str = ""
    fks: list[tuple[str, str, str]] = field(default_factory=list)  # (column, ref_table, ref_column)

    def col(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name.lower() == name.lower()), None)


@dataclass
class DatabaseCatalog:
    db_id: str
    dialect: str  # "sqlite" | "duckdb"
    path: str
    tables: list[Table]

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name.lower() == name.lower()), None)

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def pii_columns(self) -> dict[str, set[str]]:
        return {t.name.lower(): {c.name.lower() for c in t.columns if c.pii} for t in self.tables}

    # ---------- rendering for prompts ----------
    def render(self, tables: list[str] | None = None, descriptions: bool = True, samples: bool = True,
               max_desc: int = 160) -> str:
        """CREATE TABLE style schema with inline comments (the format BIRD's own baseline uses)."""
        chosen = [t for t in self.tables if tables is None or t.name.lower() in {x.lower() for x in tables}]
        q = (lambda n: f"`{n}`") if self.dialect == "sqlite" else (lambda n: f'"{n}"' if not n.isidentifier() else n)
        out = []
        for t in chosen:
            head = f"CREATE TABLE {q(t.name)} ("
            if descriptions and t.description:
                head = f"-- {t.description}\n" + head
            items: list[tuple[str, str]] = []  # (sql, comment)
            for c in t.columns:
                comment = []
                if descriptions:
                    d = c.description.strip()
                    if c.value_description.strip():
                        d = (d + "; " if d else "") + c.value_description.strip()
                    if d:
                        comment.append(d[:max_desc])
                if samples and c.samples and not c.pii:  # never show PII values, even tokenized
                    comment.append("example: " + json.dumps(c.samples[:3], ensure_ascii=False)[:120])
                if c.pii:
                    comment.append("PII (masked)")
                items.append((f"{q(c.name)} {c.type}", " | ".join(comment).replace("\n", " ")))
            pk = [c.name for c in t.columns if c.pk]
            if pk:
                items.append((f"PRIMARY KEY ({', '.join(q(p) for p in pk)})", ""))
            for col, rt, rc in t.fks:
                items.append((f"FOREIGN KEY ({q(col)}) REFERENCES {q(rt)}({q(rc)})", ""))
            lines = []
            for i, (sql, comment) in enumerate(items):
                line = "    " + sql + ("," if i < len(items) - 1 else "")
                lines.append(line + (f" -- {comment}" if comment else ""))
            out.append(head + "\n" + "\n".join(lines) + "\n);")
        return "\n\n".join(out)

    def doc_for_table(self, t: Table) -> str:
        """Text used to index a table for retrieval."""
        parts = [t.name, t.name.replace("_", " "), t.description]
        for c in t.columns:
            parts += [c.name, c.name.replace("_", " "), c.description, c.value_description[:200], " ".join(c.samples[:3])]
        return " ".join(p for p in parts if p)


# ---------------- BIRD (SQLite) ----------------
def _read_desc_csv(path: Path) -> dict[str, dict[str, str]]:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    else:
        return {}
    out = {}
    for r in rows:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        name = r.get("original_column_name", "")
        if name:
            desc = r.get("column_description", "") or r.get("column_name", "")
            out[name.lower()] = {"description": desc, "value_description": r.get("value_description", "")}
    return out


def _sqlite_samples(con: sqlite3.Connection, table: str, col: str, k: int = 3) -> list[str]:
    try:
        rows = con.execute(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT {k}').fetchall()
    except sqlite3.Error:
        return []
    return [str(r[0])[:40] for r in rows]


def build_bird_catalog(db_id: str, root: Path = BIRD_DB_ROOT) -> DatabaseCatalog:
    path = root / db_id / f"{db_id}.sqlite"
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    desc_dir = root / db_id / "database_description"
    tables = []
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for tname in names:
        descs = {}
        for cand in (desc_dir / f"{tname}.csv", desc_dir / f"{tname.lower()}.csv"):
            if cand.exists():
                descs = _read_desc_csv(cand)
                break
        cols = []
        for _cid, cname, ctype, _nn, _d, pk in con.execute(f'PRAGMA table_info("{tname}")'):
            d = descs.get(cname.lower(), {})
            cols.append(Column(cname, ctype or "TEXT", d.get("description", ""), d.get("value_description", ""),
                               _sqlite_samples(con, tname, cname), bool(pk)))
        fks = [(r[3], r[2], r[4] or r[3]) for r in con.execute(f'PRAGMA foreign_key_list("{tname}")')]
        tables.append(Table(tname, cols, "", fks))
    con.close()
    return DatabaseCatalog(db_id, "sqlite", str(path), tables)


# ---------------- Olist (DuckDB) ----------------
def build_olist_catalog(path: Path = OLIST_DB, db_id: str = "olist") -> DatabaseCatalog:
    docs = yaml.safe_load(open(CATALOG_DOCS / "olist.yaml"))["tables"]
    con = duckdb.connect(str(path), read_only=True)
    tables = []
    names = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name").fetchall()]
    pk_map = {"orders": ["order_id"], "customers": ["customer_id"], "products": ["product_id"],
              "sellers": ["seller_id"], "order_items": ["order_id", "order_item_id"],
              "order_payments": ["order_id", "payment_sequential"],
              "product_category_name_translation": ["product_category_name"]}
    fk_map = {
        "orders": [("customer_id", "customers", "customer_id")],
        "order_items": [("order_id", "orders", "order_id"), ("product_id", "products", "product_id"),
                        ("seller_id", "sellers", "seller_id")],
        "order_payments": [("order_id", "orders", "order_id")],
        "order_reviews": [("order_id", "orders", "order_id")],
        "products": [("product_category_name", "product_category_name_translation", "product_category_name")],
        "customers": [("customer_zip_code_prefix", "geolocation", "geolocation_zip_code_prefix")],
        "sellers": [("seller_zip_code_prefix", "geolocation", "geolocation_zip_code_prefix")],
    }
    for tname in names:
        d = docs.get(tname, {})
        pii = {p.lower() for p in d.get("pii", [])}
        cols = []
        for cname, ctype in con.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='main' AND "
                "table_name=? ORDER BY ordinal_position", [tname]).fetchall():
            samples = [str(r[0])[:40] for r in con.execute(
                f'SELECT DISTINCT "{cname}" FROM main."{tname}" WHERE "{cname}" IS NOT NULL LIMIT 3').fetchall()]
            cols.append(Column(cname, ctype, d.get("columns", {}).get(cname, ""), "", samples,
                               cname in pk_map.get(tname, []), cname.lower() in pii))
        tables.append(Table(tname, cols, d.get("description", ""), fk_map.get(tname, [])))
    con.close()
    return DatabaseCatalog(db_id, "duckdb", str(path), tables)


_REGISTRY: dict[str, DatabaseCatalog] = {}


def register_catalog(cat: DatabaseCatalog) -> None:
    """Make an in-memory catalog available under its db_id (tests, ad-hoc databases)."""
    _REGISTRY[cat.db_id] = cat


def get_catalog(db_id: str) -> DatabaseCatalog:
    if db_id in _REGISTRY:
        return _REGISTRY[db_id]
    return _get_catalog_cached(db_id)


def build_duckdb_catalog(path: str, db_id: str) -> DatabaseCatalog:
    """Generic catalog for any DuckDB file (no human docs): used by tests and ad-hoc databases."""
    con = duckdb.connect(path, read_only=True)
    tables = []
    for (tname,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' "
                                "ORDER BY table_name").fetchall():
        cols = [Column(c, t) for c, t in con.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='main' AND table_name=? "
            "ORDER BY ordinal_position", [tname]).fetchall()]
        tables.append(Table(tname, cols))
    con.close()
    return DatabaseCatalog(db_id, "duckdb", path, tables)


@lru_cache(maxsize=32)
def _get_catalog_cached(db_id: str) -> DatabaseCatalog:
    """Cached catalog (JSON on disk, so sample-value scans run once)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cpath = CACHE / f"{db_id}.json"
    if cpath.exists():
        raw = json.loads(cpath.read_text())
        tables = [Table(t["name"], [Column(**c) for c in t["columns"]], t["description"],
                        [tuple(x) for x in t["fks"]]) for t in raw["tables"]]
        return DatabaseCatalog(raw["db_id"], raw["dialect"], raw["path"], tables)
    if db_id == "olist":
        cat = build_olist_catalog()
    elif db_id == "olist_redteam":
        cat = build_olist_catalog(REDTEAM_DB, "olist_redteam")
    else:
        cat = build_bird_catalog(db_id)
    cpath.write_text(json.dumps(asdict(cat), ensure_ascii=False))
    return cat
