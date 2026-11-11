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
  concurrent red-team scenario. Over-denial can still *mask* a leak (a suppressed answer cannot leak), so
  round 1 is treated as possibly contaminated; the final round was run alone.
- **Fix / prevention.** The final red-team round was re-run alone; RUNBOOK notes that permission harnesses
  run sequentially (or each against its own database, as the unit tests do with `copilot_test`).

## I-4. Channel answers could be posted publicly when the channel had more than one page of members (found by the acceptance review)
- **Symptom.** An independent acceptance review reproduced a public post of a restricted
  answer in a channel whose unentitled member was on the second page of `conversations.members`.
- **Root cause.** The audience check read only the first page (ignored `next_cursor`), and an empty or failed
  member lookup was treated as "safe to post publicly".
- **Fix.** All pages are enumerated; any lookup failure or incomplete enumeration fails closed (ephemeral). The
  emulator now paginates, and the red-team oracle takes the readers from the emulator's own membership list,
  not from the bot's lookup (the old oracle shared the bot's blind spot).
- **Prevention.** `test_audience_is_enumerated_across_pages`, `test_audience_lookup_failure_fails_closed`.
- **Why the red team missed it.** No scenario used a channel larger than one page; the leak was in delivery,
  which the oracle trusted.

## I-5. A revocation during the audience lookup still delivered the answer to the asker (found by the acceptance review)
- **Root cause.** The delivery-time check ran inside `Copilot.ask`; the Slack layer then spent further calls
  (members, users.info) before sending, without re-checking.
- **Fix.** The Slack layer re-authorises the asker and every dependency immediately before sending, and
  discards the answer otherwise. Test: `test_revocation_during_audience_lookup_discards`.

## I-6. Deleted text survived in cached BM25 statistics; traces carried model output text (found by the acceptance review)
- **Root cause.** The per-allowed-set BM25 cache was keyed by chunk ids, so a deletion or update could reuse
  statistics computed with the old text; LLM spans in traces stored the full response.
- **Fix.** The BM25 cache key includes each chunk's content hash; LLM spans keep usage only. Embedding purges
  persist in production (`persist_purges`); harnesses save additively so shared replay caches stay intact.

## I-7. Over-clarification when the definition was stated in other words (found by the eval)
- **Symptom.** 1 of 8 questions that already named a definition ("counting only requests with status Completed")
  was answered with "which definition do you want?" (12.5%; target <= 10%).
- **Root cause.** Variant detection matched a short list of literal phrases ("completed only", "excluding
  canceled"); any other way of stating the same definition fell through to "ambiguous".
- **Fix (general, not per question).** Each variant carries `signals`: regexes for how people state that definition
  (only/just ... completed, status Completed, excluding/leaving out cancellations, including/counting canceled,
  completed or canceled, every closed request, of/among all requests, open ... as misses). Still deterministic; if
  both or neither variant is named, the copilot asks.
- **Check.** 14 paraphrases written for the test and absent from every eval set (`test_definition_signals.py`);
  on the full question set 0 of 9 explicit-definition questions were asked back and all 9 ambiguous ones still were.

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
