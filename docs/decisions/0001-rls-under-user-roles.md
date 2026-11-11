# 0001. Warehouse access runs as the asker's own Postgres role with RLS

**Decision.** One NOLOGIN role per directory user; the app's gateway login has no privileges and can only
`SET LOCAL ROLE` into them; one RLS policy intersects the synced role->department table with the live
entitlements set per transaction.

**Rejected.** (a) One service account + `WHERE dept_code IN (...)` added by the app: a single prompt
injection or SQL-generation bug that drops the filter returns every department. (b) A session setting
alone (`app.user_depts`): forgeable by `set_config()` from inside a SELECT.

**Measured reason.** The RLS tests run with the SQL guard bypassed: forged settings, `SET ROLE`,
`RESET ROLE`, multi-statement strings and `pg_stats` all fail to widen access (tests/v2/test_rls.py, 25
tests); the lag experiment shows 0 SQL leaks inside the sync window with live narrowing vs 15/15 probes
leaking without it (results/v2/lag_window.json).
