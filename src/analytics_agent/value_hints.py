"""Value grounding for self-correction.

When a query returns nothing, the usual cause is a literal that does not match the data
('Unknown' vs 'No Colour', 'female' vs 'F', '2017/01' vs '2017-01'). We parse the SQL, find
column-vs-string-literal predicates, and show the model what values those columns really hold.
This is cheap (a few DISTINCT queries) and mirrors the value-retrieval step of CHESS (Talaei et al. 2024).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from analytics_agent.catalog import DatabaseCatalog
from analytics_agent.executor import execute


def _string_predicates(tree: exp.Expression) -> list[tuple[exp.Column, str]]:
    out = []
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.Like, exp.ILike, exp.In):
        cols = [c for c in (node.this, node.args.get("expression")) if isinstance(c, exp.Column)]
        lits = [x for x in node.find_all(exp.Literal) if x.is_string]
        if isinstance(node.this, exp.Func) and not cols:  # LOWER(col) = 'x'
            cols = list(node.this.find_all(exp.Column))
        if cols and lits:
            out.append((cols[0], lits[0].this))
    return out


def value_hints(sql: str, cat: DatabaseCatalog, max_cols: int = 4, max_values: int = 25) -> str:
    try:
        tree = sqlglot.parse_one(sql, read=cat.dialect)
    except Exception:  # noqa: BLE001
        return ""
    alias = {}
    for t in tree.find_all(exp.Table):
        alias[(t.alias_or_name or t.name).lower()] = t.name
        alias[t.name.lower()] = t.name
    lines, seen = [], set()
    q = (lambda n: f"`{n}`") if cat.dialect == "sqlite" else (lambda n: f'"{n}"')
    for col, literal in _string_predicates(tree):
        cname = col.name
        tables = [alias[col.table.lower()]] if col.table and col.table.lower() in alias else [
            t.name for t in cat.tables if t.name in alias.values() and t.col(cname)]
        for tname in tables[:1]:
            t = cat.table(tname)
            if t is None or t.col(cname) is None or (tname, cname) in seen:
                continue
            seen.add((tname, cname))
            if t.col(cname).pii:
                continue
            like = literal.replace("'", "''").replace("%", "")[:30]
            r1 = execute(f"SELECT DISTINCT {q(cname)} FROM {q(tname)} WHERE lower(CAST({q(cname)} AS TEXT)) LIKE "
                         f"lower('%{like}%') LIMIT 10", cat.dialect, cat.path, timeout_s=5, row_limit=10)
            r2 = execute(f"SELECT {q(cname)}, count(*) AS n FROM {q(tname)} WHERE {q(cname)} IS NOT NULL GROUP BY 1 "
                         f"ORDER BY n DESC LIMIT {max_values}", cat.dialect, cat.path, timeout_s=5, row_limit=max_values)
            similar = [str(r[0]) for r in r1.rows] if r1.ok else []
            common = [str(r[0]) for r in r2.rows] if r2.ok else []
            lines.append(f"- {tname}.{cname} (you used '{literal}'): values containing it: {similar[:10]}; "
                         f"most common values: {common[:max_values]}")
            if len(lines) >= max_cols:
                break
    return ("Actual values in the columns you filtered on:\n" + "\n".join(lines) + "\n") if lines else ""
