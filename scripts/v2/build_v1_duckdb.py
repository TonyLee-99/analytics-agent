"""DuckDB copy of the same 311 window for the v1 baseline agent (v1 runs on DuckDB/SQLite only).
No permissions: v1 has no notion of users, which is the point of the comparison.

    uv run python scripts/v2/build_v1_duckdb.py
"""

import duckdb

from analytics_agent.copilot.settings import SOURCE_WAREHOUSE, V2_STATE, WINDOW_END, WINDOW_START

V2_STATE.mkdir(parents=True, exist_ok=True)
out = V2_STATE / "c311.duckdb"
out.unlink(missing_ok=True)
con = duckdb.connect(str(out))
con.execute(f"ATTACH '{SOURCE_WAREHOUSE}' AS src (READ_ONLY)")
con.execute("CREATE TABLE department AS SELECT * FROM (VALUES " + ", ".join(
    f"('{c}', '{n.replace(chr(39), chr(39) * 2)}')" for n, c in
    __import__("analytics_agent.copilot.departments", fromlist=["x"]).DEPT_CODES.items()) + ") t(dept_code, department)")
con.execute(f"""CREATE TABLE service_request AS
SELECT f.sr_number, rt.sr_type, rt.sr_short_code, coalesce(dep.dept_code, 'UNK') AS dept_code, cd.department AS created_department,
       o.origin, f.status, f.is_open, f.is_canceled, f.is_duplicate, f.parent_sr_number, f.created_at, f.closed_at,
       f.last_modified_at, ca.community_area, w.ward
FROM src.main.fct_service_request f
LEFT JOIN src.main.dim_request_type rt USING (request_type_key)
LEFT JOIN src.main.dim_department d ON d.department_key = f.owner_department_key
LEFT JOIN department dep ON dep.department = d.department
LEFT JOIN src.main.dim_department cd ON cd.department_key = f.created_department_key
LEFT JOIN src.main.dim_origin o USING (origin_key)
LEFT JOIN src.main.dim_community_area ca USING (community_area_key)
LEFT JOIN src.main.dim_ward w USING (ward_key)
WHERE f.created_at >= TIMESTAMP '{WINDOW_START}' AND f.created_at < TIMESTAMP '{WINDOW_END}'""")
con.execute("CREATE TABLE community_area AS SELECT community_area, community_area_name, population_acs "
            "FROM src.main.dim_community_area WHERE community_area IS NOT NULL")
con.execute("CREATE TABLE request_benchmark AS SELECT sr_type_upper AS sr_type, benchmark_days "
            "FROM src.reference.ref_resolution_benchmark")
print(con.execute("SELECT count(*) FROM service_request").fetchone()[0], "rows ->", out)
