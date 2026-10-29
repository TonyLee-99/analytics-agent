"""Table-retrieval recall (no LLM calls).

recall@k = share of questions for which *every* gold table is in the retrieved set (strict: one
missing table usually means a wrong query). Reported per method, k, and with/without FK bridging,
both inside each database and at "warehouse scale" (all 11 BIRD DBs + Olist pooled into one index
of 85 tables, the setting Pinterest/LinkedIn face).
Run: uv run python -m analytics_agent.eval.retrieval_eval
"""

from __future__ import annotations

import json
import statistics

from analytics_agent.catalog import DatabaseCatalog, Table, get_catalog
from analytics_agent.config import RESULTS
from analytics_agent.eval.datasets import load_bird, load_olist_golden
from analytics_agent.retrieval import SchemaRetriever


def within_db(tasks, methods=("bm25", "embed", "hybrid"), ks=(1, 2, 3, 4, 5, 6, 8)):
    out = {}
    retrievers = {}
    for m in methods:
        for expand in (False, True):
            key = f"{m}{'+fk' if expand else ''}"
            hits = {k: 0 for k in ks}
            sizes = {k: [] for k in ks}
            for t in tasks:
                r = retrievers.setdefault((m, t.db_id), SchemaRetriever(get_catalog(t.db_id), m))
                ranked = r.rank(t.question + " " + t.evidence)
                gold = set(t.gold_tables)
                for k in ks:
                    top = [n for n, _ in ranked[:k]]
                    chosen = top + (r._bridge(top) if expand else [])
                    sizes[k].append(len(chosen))
                    if gold <= {c.lower() for c in chosen}:
                        hits[k] += 1
            out[key] = {f"recall@{k}": round(hits[k] / len(tasks), 4) for k in ks} | {
                f"avg_tables@{k}": round(statistics.mean(sizes[k]), 2) for k in ks}
    return out


def warehouse_scale(tasks, db_ids, method="bm25", ks=(5, 10, 20)):
    """Pool every table of every DB; table names are prefixed with db_id to keep them unique."""
    tables = []
    for db in db_ids:
        for t in get_catalog(db).tables:
            tables.append(Table(f"{db}.{t.name}", t.columns, t.description, []))
    cat = DatabaseCatalog("warehouse", "sqlite", "", tables)
    r = SchemaRetriever(cat, method)
    res = {}
    db_hits = {k: 0 for k in ks}
    tbl_hits = {k: 0 for k in ks}
    for t in tasks:
        ranked = [n for n, _ in r.rank(t.question + " " + t.evidence)]
        gold = {f"{t.db_id}.{g}".lower() for g in t.gold_tables}
        for k in ks:
            top = {x.lower() for x in ranked[:k]}
            tbl_hits[k] += gold <= top
        db_hits_top1 = ranked[0].split(".")[0] == t.db_id
        db_hits[ks[0]] += db_hits_top1
    res["n_tables_in_index"] = len(tables)
    res["db_routing_top1_acc"] = round(db_hits[ks[0]] / len(tasks), 4)
    for k in ks:
        res[f"all_gold_tables_recall@{k}"] = round(tbl_hits[k] / len(tasks), 4)
    return res


def main() -> None:
    bird = load_bird()
    olist = load_olist_golden()
    report = {
        "bird_within_db": within_db(bird),
        "olist_within_db": within_db(olist),
    }
    dbs = sorted({t.db_id for t in bird}) + ["olist"]
    report["warehouse_scale_bird+olist"] = {m: warehouse_scale(bird + olist, dbs, m) for m in ("bm25", "embed", "hybrid")}
    out = RESULTS / "retrieval_recall.json"
    out.write_text(json.dumps(report, indent=2))
    for k, v in report.items():
        print(k)
        for m, d in v.items():
            print("  ", m, {x: y for x, y in d.items() if "recall" in str(x) or "acc" in str(x)} if isinstance(d, dict) else d)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
