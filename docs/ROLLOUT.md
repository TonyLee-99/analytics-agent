# Rollout plan

**Phase 0 — shadow (2 weeks).** Bot runs on real questions in one analytics channel, answers sent only
to the analytics team for review. Guardrails: leak oracle on every trace (cited/context docs vs live
ACL, SQL rows vs policy-model rerun on a sample), judge-scored sample of 20/day. Exit: 0 leaks, >= 70%
correct on the sampled questions, p95 latency < 30 s.

**Phase 1 — limited (one department + 311 analytics, ~10 users).** Ephemeral-by-default in channels.
Guardrail metrics on a dashboard: leak-oracle hits (page on any), recheck drops/day (spike = sync broken),
clarification rate, 👎 rate and reasons, cost/answer, cache hit rate. Exit: 2 weeks without a leak,
👎 < 20%.

**Phase 2 — GA (all departments).** Directory sync lag SLO < 5 min; weekly red-team replay in CI.

**Kill switch.** `AA_COPILOT_DISABLED=1` (bot replies with a maintenance message); per-route switches
`AA_DISABLE_SQL=1`, `AA_DISABLE_DOCS=1`; revoke the Slack app token as last resort. Flushing the answer
cache is a restart (in-memory).

**Handover checklist.** Runbook walkthrough with on-call; owner for org.yaml/doc_acl.yaml mapping; owner
for chicago311.yaml metric definitions (one per family); access review of gateway role; red-team set
ownership; dashboards and alerts wired; Slack app tokens in the secret store, rotation date set.
