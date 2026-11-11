# Incidents (v2)

Real problems hit while building and testing v2, in the order found. Each has symptom, root cause,
fix and prevention. v1's security findings are in `docs/v1/security.md`.

## I-1. A second SQL statement ran despite "one statement only" (found by a smoke test, before any eval)
- **Symptom.** `SELECT 1; SELECT 2` sent through the warehouse gateway executed both statements; `RESET ROLE`
  sent as a query executed too (the transaction was rolled back afterwards, so nothing persisted).
- **Root cause.** The design assumed psycopg 3 always uses the extended query protocol. It does not:
  `execute()` without bind parameters uses the simple protocol, which accepts multi-statement strings.
- **Fix.** Queries are sent with `binary=True`, which forces the extended protocol; the server then
  rejects multiple commands ("cannot insert multiple commands into a prepared statement").
- **Prevention.** `tests/v2/test_rls.py::test_multi_statement_rejected_by_server` runs the gateway with the
  SQL guard bypassed, so the database layer is tested on its own.

## I-2. Governed queries silently dropped because the planner wrote `"op": "eq"` (found on the development half)
- **Symptom.** On the author-written (development) half of the eval, "top 5 request types for Water Management"
  and "median days to close for rodent baiting" returned no data and the answer said the question "could not be
  expressed with the governed metrics".
- **Root cause.** The planner emitted filter operators as words (`eq`); the v1 compiler accepts only SQL
  operators and raised `SemanticError`, which the copilot treated as "not expressible".
- **Fix.** Operator aliases are normalised before compiling (`OP_ALIASES` in copilot.py). Both items were re-run.
- **Prevention.** The trace records `semantic_error`; the eval report lists items whose SQL path produced no
  rows. Found and fixed on the author half only; the codex-blind half was not used for tuning.

## I-3. Two harnesses shared one Postgres role table (found while running the red team)
- **Symptom.** None observed in the results; found by reading the run log: the revocation-lag experiment ran
  while red-team round 1 was in progress, and both rewrite `acl.role_departments` in the same database.
- **Root cause.** Every deployment syncs the directory into the single `copilot` database; the harnesses
  assumed exclusive use.
- **Impact.** The lag experiment only removes memberships, so the overlap could only cause over-denial in a
  concurrent red-team scenario, never a leak. Round 1's 0 leaks is therefore not inflated by it.
- **Fix / prevention.** The final red-team round was re-run alone; RUNBOOK notes that permission harnesses
  run sequentially (or each against its own database, as the unit tests do with `copilot_test`).

## Postmortem: I-1 (multi-statement execution through the gateway)
**Summary.** A design assumption about the database driver was false: the gateway would execute a string with
several statements. It was caught by a smoke test on day one, before any evaluation, and never reached a user.

**Timeline (2026-11-11).** 03:45 RLS smoke test sends `SELECT 1; SELECT 2` and `RESET ROLE` as probes;
both execute. 03:47 confirmed in isolation: psycopg 3 with no parameters uses the simple query protocol.
03:48 fix (`binary=True` forces the extended protocol); probe now fails with "cannot insert multiple
commands into a prepared statement". 03:50 regression test added.

**Impact.** None in practice: every gateway transaction is `BEGIN READ ONLY ... ROLLBACK`, the SQL guard
rejects multiple statements, and `set_config` is not executable by user roles. But the database layer was
meant to hold on its own if the guard failed, and for multi-statement strings it did not: a guard bypass
plus `SET LOCAL app.live_depts = '...'; SELECT ...` could have restored *synced but already revoked*
departments during the sync window (the live setting is the only thing narrowing them then).

**Root cause.** "One statement over the extended protocol" was written into the design from memory of the
driver, not verified. The test suite tested the guard, not the database layer with the guard off.

**What went well.** Defence in depth: three other layers (guard, read-only transaction, revoked
`set_config`) meant no exploitable path existed. The smoke test probed the layer directly.

**Action items (done).** Extended protocol forced; `test_multi_statement_rejected_by_server` and the
other session-trick tests run every probe with the guard bypassed; design doc corrected; FEEDBACK_TO_PRODUCT
item 1 suggests a platform-level lint for DB wrappers.
