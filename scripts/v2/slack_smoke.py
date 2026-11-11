"""Live smoke test: answer one question in the owner's DM through the real Slack Web API.

Uses the same SlackCopilot core as the bot, with an event built locally (Slack does not let a bot send itself a
user message). Tokens come from the environment (`make slack-smoke` reads them from the keychain).

    make slack-smoke Q="How many pothole complaints did CDOT get last month?"
"""

import os
import sys
from pathlib import Path

import yaml
from slack_sdk import WebClient

from analytics_agent.copilot.copilot import Copilot, build_deployment
from analytics_agent.copilot.identity import apply_local_identity
from analytics_agent.copilot.settings import ROOT
from analytics_agent.copilot.slack_app import SlackCopilot, to_mrkdwn

question = " ".join(sys.argv[1:]) or "How many pothole complaints did CDOT get last month?"
client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
team = client.auth_test()["team_id"]
local = ROOT / "config" / "local_identity.yaml"
email = yaml.safe_load(local.read_text())["mappings"][0]["slack_email"].lower()
user = client.users_lookupByEmail(email=email)["user"]["id"]
dm = next(c["id"] for c in client.conversations_list(types="im", limit=200)["channels"] if c.get("user") == user)
dep = build_deployment()
apply_local_identity(dep.directory, Path(local))
dep.index.sync(now=dep.clock() + dep.index.lag_s)
core = SlackCopilot(Copilot(dep), client, team)
rec = core.handle_message(team, {"type": "message", "channel_type": "im", "channel": dm, "user": user,
                                 "text": question, "ts": "0"}, None)
ans = rec.get("answer")
print("delivery:", rec["delivery"], "| ts:", rec.get("ts"))
if ans is not None:
    sent = to_mrkdwn(ans.text)
    print("status:", ans.status, "| has '**':", "**" in sent, "| internal labels:",
          any(w in ans.text for w in ("SOURCES", "DATA", "NOTICES")))
    print(sent)
