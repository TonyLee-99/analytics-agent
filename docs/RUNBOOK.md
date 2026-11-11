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
  (evicted), LLM response cache (dependency-tracked purge, `purge_llm_on_delete`), traces (ids, spans and usage; no document or model text), SQL cassette and demo site (public documents only).

## Slack emulator vs real Slack
Same Bolt app and WebClient; the emulator skips OAuth/signing, rate-limit tiers (faults are injected),
Block Kit validation (pagination is emulated). The live Socket Mode path runs in the owner's workspace since 2026-11-11 (`make slack-live`, `make slack-smoke`).

## Going live on Slack (owner steps)
1. Create the app from `docs/slack_manifest.yaml` (api.slack.com/apps -> From a manifest). Scopes and why:
   `app_mentions:read` + `app_mention` (channel questions), `im:history` + `message.im` (DMs), `chat:write`
   (answers and ephemeral replies), `users:read` + `users:read.email` (identity mapping by verified email),
   `channels:read` / `groups:read` / `mpim:read` / `im:read` (`conversations.members` for the audience check),
   interactivity on (feedback buttons arrive over Socket Mode). No channel-history scope: in channels, follow-up
   replies in a thread must @mention the bot (least privilege).
2. Create an app-level token with `connections:write` (Socket Mode), install the app, then store both tokens in
   the keychain: `security add-generic-password -s slack-bot-token -a copilot -w` and the same for
   `slack-app-token` (each prompts for the value; nothing is written to a file).
3. Identity: your Slack email is not in the Simulated directory. Copy `config/local_identity.example.yaml` to
   `config/local_identity.yaml` (gitignored), set your email and a demo user (`maria` = CDOT, `dev` = Streets and
   Sanitation, `priya` = Water, `lena` = all departments); restart to switch. The override refuses an email that
   already belongs to another directory user.
4. `make pg-start` then `make slack-live`. DM the bot, or invite it to a channel and @mention it.
