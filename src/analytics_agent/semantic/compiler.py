"""Deterministic compiler: governed metric query (JSON) -> DuckDB SQL.

The LLM never writes metric math in this mode. It picks metric names, dimensions, filters and a time
window; this module turns that into SQL using the single definition in metrics.yaml. That is the whole
point of a semantic layer: "GMV" means one thing, no matter who (or what) asks.

Query spec (what the LLM emits):
{
  "metrics": ["gmv", "orders"],
  "dimensions": ["customer_state"],
  "time_grain": "month" | "quarter" | "year" | "week" | "day" | null,
  "time_range": {"start": "2017-01-01", "end": "2018-01-01"},      # start inclusive, end exclusive
  "filters": [{"field": "customer_state", "op": "=", "value": "SP"}],
  "order_by": [{"field": "gmv", "desc": true}],
  "limit": 10
}
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from analytics_agent.config import SEMANTIC_SPEC

GRAINS = {"day", "week", "month", "quarter", "year"}
OPS = {"=", "!=", "<>", ">", "<", ">=", "<=", "in", "not in", "like"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$")


class SemanticError(ValueError):
    """Raised when a metric query references something the governed layer does not define."""


@dataclass(frozen=True)
class SemanticLayer:
    spec: dict[str, Any]

    @property
    def metrics(self) -> dict[str, Any]:
        return self.spec["metrics"]

    @property
    def models(self) -> dict[str, Any]:
        return self.spec["models"]

    @property
    def dimensions(self) -> dict[str, Any]:
        return self.spec["dimensions"]

    def allowed_dims(self, metric: str) -> set[str]:
        m = self.metrics[metric]
        if m["type"] == "custom_sql":
            return set()
        if "allowed_dimensions" in m:
            return set(m["allowed_dimensions"])
        model = m["model"]
        dims = set()
        for name, d in self.dimensions.items():
            models = d.get("models")
            if models is None and model != "customer_orders" or models is not None and model in models:
                dims.add(name)
        return dims

    def catalog_text(self) -> str:
        """Human/LLM-readable catalog of governed metrics and dimensions (goes into the prompt)."""
        lines = ["GOVERNED METRICS (use these names exactly):"]
        for name, m in self.metrics.items():
            dims = sorted(self.allowed_dims(name))
            lines.append(f"- {name}: {m['label']}. {m['description']}")
            if m.get("caveats"):
                lines.append(f"    caveat: {m['caveats']}")
            lines.append(f"    dimensions: {', '.join(dims) if dims else '(none, whole-history scalar)'}")
        lines.append("DIMENSIONS:")
        for name, d in self.dimensions.items():
            lines.append(f"- {name}: {d['description']}")
        lines.append("TIME: time_grain in {day, week, month, quarter, year} buckets the metric's time column "
                     "(purchase timestamp) into a column named `period`; time_range filters it (end exclusive).")
        return "\n".join(lines)


@lru_cache(maxsize=4)
def load_layer(path: str | Path = SEMANTIC_SPEC) -> SemanticLayer:
    with open(path) as f:
        return SemanticLayer(yaml.safe_load(f))


def _literal(v: Any) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    raise SemanticError(f"unsupported filter value: {v!r}")


def _filter_sql(f: dict[str, Any], allowed: set[str]) -> str:
    field, op, value = f.get("field"), str(f.get("op", "=")).lower(), f.get("value")
    if field not in allowed:
        raise SemanticError(f"filter field '{field}' is not a governed dimension for this metric")
    if op not in OPS:
        raise SemanticError(f"unsupported operator '{op}'")
    if op in {"in", "not in"}:
        if not isinstance(value, list) or not value:
            raise SemanticError("IN filters need a non-empty list")
        return f"{field} {op.upper()} ({', '.join(_literal(v) for v in value)})"
    return f"{field} {op.upper()} {_literal(value)}"


def compile_query(q: dict[str, Any], layer: SemanticLayer | None = None) -> str:
    layer = layer or load_layer()
    metrics = q.get("metrics") or []
    if not metrics:
        raise SemanticError("query must name at least one metric")
    unknown = [m for m in metrics if m not in layer.metrics]
    if unknown:
        raise SemanticError(f"unknown metric(s): {unknown}. Known: {sorted(layer.metrics)}")
    dims: list[str] = list(q.get("dimensions") or [])
    grain = q.get("time_grain")
    if grain is not None and grain not in GRAINS:
        raise SemanticError(f"time_grain must be one of {sorted(GRAINS)}")
    tr = q.get("time_range") or {}
    for k in ("start", "end"):
        if tr.get(k) is not None and not DATE_RE.match(str(tr[k])):
            raise SemanticError(f"time_range.{k} must be YYYY-MM-DD")

    customs = [m for m in metrics if layer.metrics[m]["type"] == "custom_sql"]
    if customs:
        if len(metrics) > 1 or dims or grain or q.get("filters") or tr:
            raise SemanticError(f"{customs[0]} is a whole-history scalar: no dimensions, filters or time window")
        return layer.metrics[customs[0]]["sql"].strip()

    out_keys = dims + (["period"] if grain else [])
    ctes = []
    for name in metrics:
        m = layer.metrics[name]
        allowed = layer.allowed_dims(name)
        bad = [d for d in dims if d not in allowed]
        if bad:
            raise SemanticError(f"metric '{name}' cannot be broken down by {bad}; allowed: {sorted(allowed)}")
        model = layer.models[m["model"]]
        tcol = model["time_column"]
        where = []
        if m.get("filter"):
            where.append(f"({m['filter']})")
        if tr.get("start"):
            where.append(f"{tcol} >= TIMESTAMP {_literal(str(tr['start']))}")
        if tr.get("end"):
            where.append(f"{tcol} < TIMESTAMP {_literal(str(tr['end']))}")
        for f in q.get("filters") or []:
            where.append(_filter_sql(f, allowed | {"order_status"} if "order_status" in allowed else allowed))
        if m["type"] == "sum":
            agg = f"sum({m['expr']})"
        elif m["type"] == "avg":
            agg = f"avg({m['expr']})"
        elif m["type"] == "count_distinct":
            agg = f"count(DISTINCT {m['expr']})"
        elif m["type"] == "ratio":
            agg = f"({m['numerator']}) * 1.0 / nullif({m['denominator']}, 0)"
        else:
            raise SemanticError(f"unsupported metric type {m['type']}")
        select = list(dims)
        if grain:
            select.append(f"CAST(date_trunc('{grain}', {tcol}) AS DATE) AS period")
        select.append(f"{agg} AS {name}")
        sql = f"SELECT {', '.join(select)}\n  FROM (\n{_indent(model['sql'].strip(), 4)}\n  ) AS m"
        if where:
            sql += "\n  WHERE " + "\n    AND ".join(where)
        if out_keys:
            sql += "\n  GROUP BY " + ", ".join(str(i + 1) for i in range(len(out_keys)))
        ctes.append((f"m_{name}", sql))

    with_sql = "WITH " + ",\n".join(f"{cte} AS (\n  {body}\n)" for cte, body in ctes)
    first = ctes[0][0]
    from_sql = first
    for cte, _ in ctes[1:]:
        from_sql += f"\nFULL OUTER JOIN {cte} USING ({', '.join(out_keys)})" if out_keys else f"\nCROSS JOIN {cte}"
    cols = out_keys + metrics
    final = f"{with_sql}\nSELECT {', '.join(cols)}\nFROM {from_sql}"
    order = q.get("order_by") or ([{"field": "period", "desc": False}] if grain else [])
    order_sql = []
    for o in order:
        if o.get("field") not in cols:
            raise SemanticError(f"order_by field '{o.get('field')}' must be one of {cols}")
        order_sql.append(f"{o['field']} {'DESC' if o.get('desc') else 'ASC'} NULLS LAST")
    if order_sql:
        final += "\nORDER BY " + ", ".join(order_sql)
    if q.get("limit") is not None:
        final += f"\nLIMIT {int(q['limit'])}"
    return final


def _indent(s: str, n: int) -> str:
    return "\n".join(" " * n + line for line in s.splitlines())
