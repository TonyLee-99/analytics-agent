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
