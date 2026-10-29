"""SQL guardrails, enforced on the parsed AST (sqlglot), never on regexes over the raw string.

Layered defence (each layer assumes the previous one failed):
1. parse   - must parse in the target dialect; exactly one statement.
2. shape   - the root must be a read query (SELECT / UNION / INTERSECT / EXCEPT, optionally under WITH);
             no DML/DDL/command node may appear anywhere in the tree (e.g. a DELETE hidden in a CTE).
3. funcs   - deny functions that read files, load code or touch the host (read_csv, load_extension, ...).
4. tables  - every referenced table must exist in the governed catalog (blocks `raw.customers`,
             `sqlite_master`, `information_schema`, attached DBs, table functions).
5. limits  - a LIMIT is injected/clamped so a cartesian product cannot stream a billion rows back.
Then the executor adds: read-only connection, statement timeout, and a hard fetch cap.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

logging.getLogger("sqlglot").setLevel(logging.ERROR)  # unsupported syntax becomes exp.Command -> blocked

READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)

# Node types that must never appear anywhere in a query we execute.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, n) for n in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterColumn", "TruncateTable",
        "Command", "Pragma", "Attach", "Detach", "Copy", "Set", "Use", "Grant", "Revoke", "Transaction",
        "Commit", "Rollback", "Analyze", "Describe", "Cache", "Uncache", "Refresh", "Install", "LoadData",
        "Into", "Export", "Kill", "Call",
    ) if hasattr(exp, n)
)

# Lower-case function names that touch files, network, extensions or the host.
DENY_FUNCTIONS = {
    # SQLite
    "load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "sqlite_compileoption_get",
    # DuckDB table/file functions
    "read_csv", "read_csv_auto", "read_parquet", "parquet_scan", "read_json", "read_json_auto",
    "read_json_objects", "read_ndjson", "read_text", "read_blob", "glob", "sniff_csv", "parquet_metadata",
    "parquet_schema", "iceberg_scan", "delta_scan", "sqlite_scan", "postgres_scan", "mysql_scan",
    "duckdb_settings", "duckdb_extensions", "duckdb_secrets", "getenv", "current_setting", "query",
    "query_table", "pg_read_file", "pg_ls_dir", "lo_import", "lo_export", "dblink", "system", "shell",
}

# Functions whose argument decides how many bytes the engine allocates (memory-DoS vectors:
# `repeat('x', 9e8)`, `zeroblob(1e9)`, `printf('%.*c', 9e8, 'x')`). Size arguments must be literals
# <= MAX_SIZE_ARG (padding functions may also take column values, which are bounded by the data).
MAX_SIZE_ARG = 100_000
SIZE_FUNCS = {"repeat": [1], "replicate": [1], "space": [0], "zeroblob": [0], "randomblob": [0],
              "lpad": [1], "rpad": [1], "pad": [1], "printf": None, "format": None}
LITERAL_ONLY = {"repeat", "replicate", "space", "zeroblob", "randomblob"}

SYSTEM_TABLE_PREFIXES = ("sqlite_", "information_schema", "pg_", "duckdb_", "temp.", "main.sqlite_")


@dataclass
class Validation:
    ok: bool
    sql: str  # possibly rewritten (LIMIT injected); safe to execute only if ok
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(self.errors)


def _dialect(d: str) -> str:
    return {"sqlite": "sqlite", "duckdb": "duckdb"}.get(d, d)


SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "sqlite_master", "sqlite_temp_master", "temp", "system"}


def _visible_ctes(table: exp.Table) -> set[str]:
    """CTE names that an unqualified table reference can resolve to, respecting scope:
    - a reference in a WITH's main body sees every CTE of that WITH;
    - a reference inside CTE i sees CTEs 0..i-1, and itself only for WITH RECURSIVE
      (DuckDB resolves a non-recursive self-reference to the *base table*, so it must be checked);
    - enclosing WITH clauses are visible too.
    v1 dropped any table whose bare name matched *any* CTE anywhere in the tree, so
    `WITH sqlite_master AS (SELECT 1) SELECT sql FROM main.sqlite_master` slipped through."""
    names: set[str] = set()
    handled: set[int] = set()
    prev: exp.Expression = table
    node = table.parent
    while node is not None:
        if isinstance(node, exp.With):
            ctes = list(node.expressions)
            idx = next((i for i, c in enumerate(ctes) if c is prev), None)
            if idx is not None:
                upto = ctes[: idx + 1] if node.args.get("recursive") else ctes[:idx]
                names |= {c.alias_or_name.lower() for c in upto}
            handled.add(id(node))
        else:
            w = (node.args.get("with_") or node.args.get("with")) if isinstance(node, exp.Expression) else None
            if isinstance(w, exp.With) and id(w) not in handled and prev is not w:
                names |= {c.alias_or_name.lower() for c in w.expressions}
                handled.add(id(w))
        prev, node = node, node.parent
    return names


def _is_system(name: str, schema: str = "") -> bool:
    n, sch = name.lower(), schema.lower()
    return n.startswith(SYSTEM_TABLE_PREFIXES) or sch in SYSTEM_SCHEMAS or sch.startswith(("pg_", "sqlite_", "duckdb_"))


def referenced_tables(tree: exp.Expression) -> list[str]:
    """Base tables a query reads: every exp.Table node that does not resolve to a visible CTE.
    Schema/catalog-qualified names are never treated as CTE references."""
    out = []
    for t in tree.find_all(exp.Table):
        name = t.name
        if not name:
            continue
        qualified = bool(t.db or t.catalog)
        if not qualified and name.lower() in _visible_ctes(t):
            continue
        out.append(".".join(p for p in (t.catalog, t.db, name) if p))
    return sorted(set(out), key=str.lower)


def validate_sql(sql: str, dialect: str, allowed_tables: list[str] | None = None, row_limit: int | None = None,
                 allowed_schemas: tuple[str, ...] = ("main",)) -> Validation:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        return Validation(False, sql, ["empty SQL"])
    d = _dialect(dialect)
    try:
        statements = [s for s in sqlglot.parse(sql, read=d) if s is not None]
    except ParseError as e:
        return Validation(False, sql, [f"parse error: {str(e).splitlines()[0][:200]}"])
    except Exception as e:  # noqa: BLE001 - sqlglot can raise TokenError etc.
        return Validation(False, sql, [f"parse error: {type(e).__name__}: {str(e)[:200]}"])
    if len(statements) != 1:
        return Validation(False, sql, [f"exactly one statement allowed, got {len(statements)}"])
    tree = statements[0]
    errors: list[str] = []

    root = tree
    if isinstance(root, exp.With):  # defensive: some dialects wrap
        root = root.this
    if not isinstance(root, READ_ROOTS):
        errors.append(f"only read queries are allowed (got {type(root).__name__.upper()})")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            errors.append(f"forbidden operation in query: {type(node).__name__.upper()}")
            break
    for f in tree.find_all(exp.Func):
        names = {f.sql_name().lower()}
        if isinstance(f, exp.Anonymous):
            names.add(str(f.this).lower())
        bad = names & DENY_FUNCTIONS
        if bad:
            errors.append(f"function not allowed: {sorted(bad)[0]}")
    for f in tree.find_all(exp.Func):
        fname = (str(f.this) if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if isinstance(f, exp.Pad):
            fname = "pad"
        if fname not in SIZE_FUNCS:
            continue
        if isinstance(f, exp.Anonymous):
            args = list(f.expressions)
        elif isinstance(f, exp.Repeat | exp.Pad | exp.Space):
            args = [f.this] + [f.args[k] for k in ("times", "expression", "fill_pattern") if f.args.get(k) is not None]
        else:
            args = [a for a in [f.this, *f.expressions] if a is not None]
        if fname in ("printf", "format"):
            # only width/precision taken from an argument (`%.*c`) or written in the format can amplify
            fmt = args[0] if args else None
            fmt_s = fmt.this if isinstance(fmt, exp.Literal) and fmt.is_string else ""
            if any(int(w) > MAX_SIZE_ARG for w in re.findall(r"%[-+ 0#]*(\d+)", fmt_s) + re.findall(r"\.(\d+)", fmt_s)):
                errors.append(f"width in {fname}() format exceeds {MAX_SIZE_ARG}")
            if "*" in fmt_s or not fmt_s:
                for a in args[1:]:
                    if not isinstance(a, exp.Literal):
                        errors.append(f"{fname}() with '*' width needs literal arguments")
                        break
                    if not a.is_string and float(a.this) > MAX_SIZE_ARG:
                        errors.append(f"size argument of {fname}() exceeds {MAX_SIZE_ARG}")
            continue
        for i in SIZE_FUNCS[fname]:
            if i >= len(args):
                continue
            a = args[i]
            if isinstance(a, exp.Neg):
                a = a.this
            if isinstance(a, exp.Literal) and not a.is_string:
                if float(a.this) > MAX_SIZE_ARG:
                    errors.append(f"size argument of {fname}() exceeds {MAX_SIZE_ARG}")
            elif fname in LITERAL_ONLY:
                errors.append(f"size argument of {fname}() must be a numeric literal")
    for t in tree.find_all(exp.Table):  # table-valued functions like read_csv('x') in FROM
        if isinstance(t.this, exp.Func) or isinstance(t.this, exp.Anonymous):
            errors.append(f"table functions are not allowed: {t.this.sql()[:60]}")

    # system catalogs are rejected on *every* table node and CTE name, resolved or not
    for t in tree.find_all(exp.Table):
        if t.name and _is_system(t.name, t.db or ""):
            errors.append(f"system table not allowed: {'.'.join(p for p in (t.catalog, t.db, t.name) if p)}")
    for c in tree.find_all(exp.CTE):
        if _is_system(c.alias_or_name):
            errors.append(f"CTE name shadows a system table: {c.alias_or_name}")
    tables = referenced_tables(tree)
    if allowed_tables is not None:
        allowed = {a.lower() for a in allowed_tables}
        for full in tables:
            parts = full.lower().split(".")
            name = parts[-1]
            schema_ok = len(parts) == 1 or (len(parts) == 2 and parts[0] in allowed_schemas)
            if full.lower().startswith(SYSTEM_TABLE_PREFIXES) or name.startswith(SYSTEM_TABLE_PREFIXES):
                errors.append(f"system table not allowed: {full}")
            elif not schema_ok:
                errors.append(f"schema not allowed: {full}")
            elif name not in allowed:
                errors.append(f"unknown table: {full}")
    if errors:
        return Validation(False, sql, sorted(set(errors), key=errors.index), tables=tables)

    warnings: list[str] = []
    out_sql = sql
    if row_limit is not None and isinstance(root, exp.Select | exp.Union | exp.Intersect | exp.Except):
        lim = tree.args.get("limit")
        if lim is None:
            warnings.append(f"LIMIT {row_limit} injected")
            out_sql = f"SELECT * FROM (\n{sql}\n) AS _guarded LIMIT {row_limit}"
        else:
            try:
                n = int(lim.expression.sql()) if lim.expression is not None else None
            except (ValueError, AttributeError):
                n = None
            if n is None or n < 0 or n > row_limit:  # LIMIT -1 means "unlimited" in SQLite
                warnings.append(f"LIMIT clamped to {row_limit}")
                out_sql = f"SELECT * FROM (\n{sql}\n) AS _guarded LIMIT {row_limit}"
    if tree.find(exp.Join) is not None:
        for j in tree.find_all(exp.Join):
            if j.args.get("on") is None and j.args.get("using") is None and j.args.get("kind", "").upper() != "CROSS" \
                    and not j.args.get("method"):
                # implicit comma join or join without condition: allowed, but flagged (possible cartesian)
                warnings.append("join without ON/USING (possible cartesian product)")
                break
    return Validation(True, out_sql, [], warnings, tables)
