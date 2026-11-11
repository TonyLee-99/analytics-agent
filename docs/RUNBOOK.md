# Runbook

## Deploy (single node)
1. `make pg-start` (or a managed Postgres 16), `make pg-load` (4.0M rows, ~1 min), which also applies
   roles/RLS (`warehouse.SECURITY_SQL`).
2. `make corpus index` (re-fetch is polite and cached), then start: Slack Socket Mode
   `SLACK_BOT_TOKEN=... SLACK_APP_TOKEN=... uv run python -m analytics_agent.copilot.slack_app`;
   HTTP `uv run uvicorn analytics_agent.serving.api:app` (set `AA_PROXY_SECRET`); MCP
   `AA_MCP_USER_EMAIL=... uv run python -m analytics_agent.serving.mcp_server`.
3. Smoke: `make check`, then one DM to the bot.

## Rollback
Code: redeploy the previous tag. Metric definitions: revert `semantic/chicago311.yaml` (answers name
the metric used, so affected answers are findable in traces). Permissions config: revert
`config/doc_acl.yaml` / `org.yaml` and run a sync; the recheck makes a bad config fail closed only if
the live source is right.

## Alerts and what to do
- **Leak oracle hit** (P1, page): disable the bot (`AA_COPILOT_DISABLED=1`), pull the trace id, write
  an incident (docs/INCIDENTS.md format), add the case to the red-team set before re-enabling.
- **Recheck drops spike**: index or role sync is behind; check sync worker; answers stay correct
  (fail closed) but recall drops.
- **Identity denials spike**: Slack app lost `users:read.email` scope or directory outage.
- **Cost/answer > 2x baseline**: check plan/answer token counts in traces; prompt regression.
- **Warehouse errors**: statement timeout (20 s gateway default) or Postgres down.

## Security notes for on-call
- The gateway role can `SET ROLE` to any `cu_<user>`: a compromised gateway crosses user boundaries.
  Its credentials are the crown jewel; it has no table privileges of its own.
- Local dev cluster uses trust auth on localhost; production uses scram + TLS.
- Secondary copies of document text: index (purged on delete), embedding cache (purged), answer cache
  (evicted), LLM response cache (dependency-tracked purge, `purge_llm_on_delete`), traces (ids and spans
  only, no text), SQL cassette and demo site (public documents only).

## Slack emulator vs real Slack
Same Bolt app and WebClient; the emulator skips OAuth/signing, rate-limit tiers (faults are injected),
pagination and Block Kit validation. The live Socket Mode path has not been run (needs the owner's
Slack app tokens).
