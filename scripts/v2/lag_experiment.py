"""P3: measure the revocation-lag window with the query-time recheck ON vs OFF (no LLM involved).

For each case a user loses a group at t=0; the index and the Postgres role table sync with a 300 s lag.
At several delays we check (a) retrieval: does any chunk of a document the user is no longer entitled to
reach the model context, and (b) SQL: does a count over the lost department return rows. "OFF" means
the retriever trusts the index copy and the warehouse is given the synced (stale) departments.

    uv run python scripts/v2/lag_experiment.py   # -> results/v2/lag_window.json (needs local Postgres)
"""

import json

from analytics_agent.copilot.copilot import build_deployment
from analytics_agent.copilot.retrieve import Retriever
from analytics_agent.copilot.settings import V2_RESULTS
from analytics_agent.eval.redteam_v2 import FakeClock

LAG = 300.0
DELAYS = [0, 60, 299, 301]
CASES = [("maria", "dept-cdot", "maria", "CDOT pothole repair target response time crews", "CDOT"),
         ("dev", "dept-dss", "dev", "rodent baiting target response time", "DSS"),
         ("priya", "dept-dwm", "priya", "water quality report gallons delivered", "DWM"),
         ("rosa", "dept-dss", "dss-forestry", "rodent baiting target response time", "DSS"),
         ("sam", "dept-dob", "sam", "time to issue permits performance metric", "DOB")]
SQL = "SELECT count(*) FROM wh.service_request WHERE dept_code = '{d}'"


def main():
    rows, totals = [], {"on": {"doc": 0, "sql": 0, "n": 0}, "off": {"doc": 0, "sql": 0, "n": 0}}
    for user, group, member, query, dept in CASES:
        for delay in DELAYS:
            clock = FakeClock()
            dep = build_deployment(lag_s=LAG, clock=clock)
            clock.advance(1)
            dep.directory.remove_member(group, member)
            clock.advance(delay)
            dep.index.sync()
            dep.wh_sync.sync()
            st = dep.directory.state
            entitled = {d for d, doc in dep.source.docs.items() if doc.acl & st.principals(user)}
            for mode in ("on", "off"):
                r = Retriever(dep.index, dep.directory, dep.source, recheck=mode == "on").search(user, query, k=6)
                bad_docs = sorted({h.chunk.doc_id for h in r.hits} - entitled)
                depts = st.data_departments(user) if mode == "on" else dep.wh_sync.replica.state.data_departments(user)
                n = dep.warehouse.query(user, depts, SQL.format(d=dept)).rows[0][0]
                rows.append({"user": user, "removed": f"{member} from {group}", "delay_s": delay, "recheck": mode,
                             "leaked_docs": bad_docs, "leaked_rows": n})
                totals[mode]["n"] += 1
                totals[mode]["doc"] += bool(bad_docs)
                totals[mode]["sql"] += bool(n)
    inside = [r for r in rows if r["delay_s"] < LAG]
    summary = {"lag_s": LAG, "delays_s": DELAYS, "cases": len(CASES), "probes_per_mode": totals["on"]["n"],
               "recheck_on": {"doc_leaks": totals["on"]["doc"], "sql_leaks": totals["on"]["sql"]},
               "recheck_off": {"doc_leaks": totals["off"]["doc"], "sql_leaks": totals["off"]["sql"]},
               "inside_window_probes_per_mode": len(inside) // 2,
               "off_leaks_inside_window": sum(1 for r in inside if r["recheck"] == "off" and (r["leaked_docs"] or r["leaked_rows"])),
               "off_leaks_after_window": sum(1 for r in rows if r["delay_s"] > LAG and r["recheck"] == "off"
                                             and (r["leaked_docs"] or r["leaked_rows"]))}
    V2_RESULTS.mkdir(parents=True, exist_ok=True)
    (V2_RESULTS / "lag_window.json").write_text(json.dumps({"summary": summary, "probes": rows}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
