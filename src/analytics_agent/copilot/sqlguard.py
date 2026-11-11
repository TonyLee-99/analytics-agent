"""Postgres SQL guard for the copilot: v1's AST validator for shape, plus two v2 allowlists.

1. Relations: every table reference must be one of ALLOWED_RELATIONS, fully qualified (or a CTE
   defined in the same query). v1 compared bare names; here `public.service_request` or a quoted
   look-alike is rejected.
2. Functions: an allowlist of node types (aggregates, date math, string basics, window functions).
   A denylist would have to know every dangerous function (set_config, pg_sleep, query_to_xml, ...);
   an allowlist fails closed when sqlglot meets something new.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from analytics_agent.guardrails import validate_sql

ALLOWED_RELATIONS = frozenset({"wh.service_request", "wh.department", "wh.community_area", "wh.request_benchmark"})

ALLOWED_FUNC_NODES = frozenset({
    "Abs", "And", "Or", "Not", "Avg", "Case", "If", "Cast", "TryCast", "Ceil", "Floor", "Coalesce", "Concat", "Count",
    "CountIf", "CurrentDate", "CurrentTimestamp", "Date", "DateTrunc", "TimestampTrunc", "Extract", "Greatest",
    "Least", "Length", "Lower", "Upper", "Max", "Min", "Nullif", "PercentileCont", "PercentileDisc", "Median",
    "Round", "Sum", "Stddev", "StddevPop", "StddevSamp", "Variance", "Trim", "Substring", "SplitPart", "StrPosition",
    "RowNumber", "Rank", "DenseRank", "Lag", "Lead", "FirstValue", "LastValue", "NthValue", "Ntile", "PercentRank",
    "CumeDist", "TimeToStr", "ToChar", "Mode", "LogicalOr", "LogicalAnd", "DateDiff", "DateAdd", "DateSub",
    "TsOrDsToDate", "Interval", "Div", "Mod", "Sqrt", "Power", "Pow", "Ln", "Log", "Exp", "Sign", "Left", "Right",
    "Replace", "Initcap", "Filter", "Bracket", "Between", "In", "Is", "Like", "ILike", "Distinct", "Unnest",
    "Corr", "CovarPop", "CovarSamp", "Quantile", "ApproxQuantile", "Struct", "Array", "MakeInterval", "DatetimeTrunc",
    "Timestamp", "StrToTime", "StrToDate", "UnixToTime", "TimeStrToTime", "Epoch", "DayOfWeek", "DayOfYear",
    "Year", "Month", "Day", "Week", "Quarter",
})
ALLOWED_ANONYMOUS = frozenset({"age", "make_date", "date_part", "to_char", "date", "justify_days", "width_bucket"})
MAX_ROWS = 1000


@dataclass
class GuardResult:
    ok: bool
    sql: str
    errors: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)


def check_sql(sql_text: str, row_limit: int = MAX_ROWS) -> GuardResult:
    base = validate_sql(sql_text, "postgres", allowed_tables=None, row_limit=row_limit)
    errors = list(base.errors)
    try:
        tree = sqlglot.parse_one((sql_text or "").strip().rstrip(";"), read="postgres")
    except Exception as e:  # noqa: BLE001 - any parse failure is a rejection
        return GuardResult(False, sql_text, errors or [f"parse error: {e}"[:200]])
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    rels = []
    for t in tree.find_all(exp.Table):
        if isinstance(t.this, exp.Func | exp.Anonymous):
            errors.append("table functions are not allowed")
            continue
        name = (t.name or "").lower()
        if not t.db and name in ctes:
            continue
        full = ".".join(p for p in (t.catalog, t.db, t.name) if p).lower()
        if t.catalog or full not in ALLOWED_RELATIONS:
            errors.append(f"relation not allowed: {full or '?'} (allowed: {', '.join(sorted(ALLOWED_RELATIONS))})")
        rels.append(full)
    for f in tree.find_all(exp.Func):
        kind = type(f).__name__
        if isinstance(f, exp.Anonymous):
            if str(f.this).lower() not in ALLOWED_ANONYMOUS:
                errors.append(f"function not allowed: {str(f.this).lower()}")
        elif kind not in ALLOWED_FUNC_NODES:
            errors.append(f"function not allowed: {f.sql_name().lower()}")
    for n in tree.find_all(exp.CurrentUser, exp.SessionUser) if hasattr(exp, "SessionUser") else []:
        errors.append(f"function not allowed: {type(n).__name__.lower()}")
    if errors:
        return GuardResult(False, sql_text, sorted(set(errors), key=errors.index), rels)
    return GuardResult(True, base.sql, [], sorted(set(rels)))
