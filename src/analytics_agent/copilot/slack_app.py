"""Slack delivery with Bolt for Python.

    Live (needs the owner's Slack app):  SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... \
                                          uv run python -m analytics_agent.copilot.slack_app
    Tests / demo: the same App against copilot/slack_emulator.py (only the WebClient base_url differs).

Identity: (team id, Slack user id) -> users.info -> verified email -> exactly one active directory user;
anything else is denied (identity.resolve_slack_user). Audience: in a channel the answer is posted
publicly only if every channel member is entitled to every source and department it used; otherwise
the asker gets it as an ephemeral message. Events are deduplicated by event_id (Slack redelivers when an
ack is late). Feedback: 👍 / 👎 buttons; 👎 asks for a reason; both stored with the trace id.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from slack_bolt import App
from slack_sdk import WebClient

from analytics_agent.copilot.copilot import Answer, Copilot
from analytics_agent.copilot.identity import IdentityError, resolve_slack_user
from analytics_agent.copilot.settings import V2_STATE

FEEDBACK_REASONS = {"wrong_number": "Wrong number", "missing_source": "Missing or wrong source",
                    "not_allowed": "Shows something I should not see", "unclear": "Unclear answer", "other": "Other"}
DENIED_TEXT = ("I can't verify who you are in the city directory, so I can't answer. Ask your admin to check that "
               "your Slack email matches your city account.")


class SlackCopilot:
    """Transport-independent core of the bot (Bolt listeners and the red-team harness both call it)."""

    def __init__(self, copilot: Copilot, client: WebClient, team_id: str,
                 feedback_path: Path = V2_STATE / "feedback.jsonl"):
        self.copilot = copilot
        self.client = client
        self.team_id = team_id
        self.feedback_path = feedback_path
        self.seen_events: OrderedDict[str, float] = OrderedDict()
        self.pending_clarify: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self.log: list[dict[str, Any]] = []

    # --- identity
    def directory_user(self, team_id: str | None, slack_user: str) -> str:
        if team_id != self.team_id:
            raise IdentityError("event from another Slack workspace")
        r = self.client.users_info(user=slack_user)
        return resolve_slack_user(r.get("user"), self.copilot.dep.directory.state).user_id

    def channel_audience(self, team_id: str, channel: str) -> list[str | None]:
        if channel.startswith("D"):  # a DM: the asker is the only reader
            return []
        members: list[str] = []
        cursor = None
        for _ in range(1000):  # every page; a partial member list must never count as "everyone"
            r = self.client.conversations_members(channel=channel, cursor=cursor, limit=200)
            members += r.get("members", [])
            cursor = (r.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        else:
            raise IdentityError("channel membership could not be enumerated")
        out: list[str | None] = []
        for m in members:
            if m == getattr(self, "bot_user_id", None):
                continue
            try:
                out.append(self.directory_user(team_id, m))
            except IdentityError:
                out.append(None)  # an unmappable member is treated as unentitled
        return out

    # --- events
    def first_delivery(self, event_id: str | None) -> bool:
        if not event_id:
            return True
        with self._lock:
            if event_id in self.seen_events:
                return False
            self.seen_events[event_id] = time.time()
            while len(self.seen_events) > 5000:
                self.seen_events.popitem(last=False)
            return True

    def handle_message(self, team_id: str, event: dict[str, Any], event_id: str | None = None) -> dict[str, Any]:
        if not self.first_delivery(event_id):
            return {"delivery": "duplicate_ignored"}
        if event.get("bot_id") or event.get("subtype"):
            return {"delivery": "ignored"}
        slack_user, channel = event.get("user", ""), event.get("channel", "")
        thread = event.get("thread_ts") or event.get("ts")
        text = _strip_mention(event.get("text", ""))
        try:
            user_id = self.directory_user(team_id, slack_user)
        except IdentityError as e:
            self.client.chat_postEphemeral(channel=channel, user=slack_user, text=DENIED_TEXT, thread_ts=thread)
            rec = {"delivery": "denied", "reason": str(e), "slack_user": slack_user}
            self.log.append(rec)
            return rec
        key = (channel, thread)
        if event.get("thread_ts") and key in self.pending_clarify:
            text = f"{self.pending_clarify.pop(key)} ({text})"
        ans = self.copilot.ask(user_id, text, slack_user=slack_user)
        if ans.status == "clarify":
            self.pending_clarify[key] = text
        try:
            audience = self.channel_audience(team_id, channel)
            public = channel.startswith("D") or (bool(audience) and self.copilot.audience_ok(ans, audience))
        except Exception:  # noqa: BLE001 - any failure enumerating the audience fails closed (private)
            audience, public = [None], False
        # re-authorise the asker right before sending: access may have changed during the lookups above
        ok, why = self.copilot.authorised(user_id, ans.doc_deps, set(ans.dept_deps))
        if not ok:
            self.client.chat_postEphemeral(channel=channel, user=slack_user, thread_ts=thread,
                                           text="Your access changed while this answer was being prepared, so it "
                                                "was discarded. Please ask again.")
            rec = {"delivery": "discarded", "reason": why, "user_id": user_id, "audience": audience}
            self.log.append(rec)
            return rec
        blocks = render_blocks(ans)
        if public:
            r = self.client.chat_postMessage(channel=channel, thread_ts=thread, text=ans.text, blocks=blocks)
            delivery = "public" if not channel.startswith("D") else "dm"
            ts = r.get("ts")
        else:
            r = self.client.chat_postEphemeral(channel=channel, user=slack_user, thread_ts=thread, text=ans.text,
                                               blocks=blocks)
            delivery, ts = "ephemeral", r.get("message_ts")
        rec = {"delivery": delivery, "user_id": user_id, "answer": ans, "ts": ts, "audience": audience}
        self.log.append(rec)
        return rec

    # --- feedback
    def record_feedback(self, slack_user: str, team_id: str, trace_id: str, vote: str, reason: str | None = None) -> dict:
        try:
            user_id = self.directory_user(team_id, slack_user)
        except IdentityError:
            user_id = None
        row = {"ts": time.time(), "trace_id": trace_id, "vote": vote, "reason": reason, "user_id": user_id,
               "slack_user": slack_user}
        self.feedback_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.feedback_path, "a") as f:
            f.write(json.dumps(row) + "\n")
        return row


def _strip_mention(text: str) -> str:
    import re

    return re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()


def render_blocks(ans: Answer) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": ans.text[:2900] or "(empty)"}}]
    if ans.notices:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "⚠️ " + " ".join(ans.notices)[:2900]}]})
    if ans.citations:
        lines = [f"[{c['tag']}] <{c['url']}|{c['title'][:80]}> · `{c['doc_id']}:{c['start']}-{c['end']}`"
                 for c in ans.citations]
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "\n".join(lines)[:2900]}]})
    if ans.sql:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Governed metrics: "
                       f"{', '.join(ans.metrics_used) or 'raw SQL'} · trace `{ans.trace_id}`"}]})
    if ans.status in ("answered", "clarify"):
        blocks.append({"type": "actions", "block_id": "feedback", "elements": [
            {"type": "button", "action_id": "feedback_up", "text": {"type": "plain_text", "text": "👍"}, "value": ans.trace_id},
            {"type": "button", "action_id": "feedback_down", "text": {"type": "plain_text", "text": "👎"},
             "value": ans.trace_id}]})
    return blocks


def reason_blocks(trace_id: str) -> list[dict[str, Any]]:
    return [{"type": "section", "text": {"type": "mrkdwn", "text": "Sorry about that. What was wrong?"}},
            {"type": "actions", "block_id": "feedback", "elements": [{
                "type": "static_select", "action_id": "feedback_reason",
                "placeholder": {"type": "plain_text", "text": "Pick a reason"},
                "options": [{"text": {"type": "plain_text", "text": label}, "value": f"{trace_id}|{key}"}
                            for key, label in FEEDBACK_REASONS.items()]}]}]


def build_app(core: SlackCopilot, token: str, client: WebClient | None = None, testing: bool = False) -> App:
    app = App(token=token, client=client or core.client, process_before_response=testing,
              token_verification_enabled=not testing, request_verification_enabled=not testing,
              ignoring_self_events_enabled=True)
    core.bot_user_id = None

    @app.event("app_mention")
    def on_mention(body, event, ack):
        ack()
        core.handle_message(body.get("team_id"), event, body.get("event_id"))

    @app.event("message")
    def on_message(body, event, ack):
        ack()
        if event.get("channel_type") == "im" or event.get("thread_ts"):
            core.handle_message(body.get("team_id"), event, body.get("event_id"))

    @app.action("feedback_up")
    def on_up(ack, body, action):
        ack()
        core.record_feedback(body["user"]["id"], body["team"]["id"], action["value"], "up")
        core.client.chat_postEphemeral(channel=body["channel"]["id"], user=body["user"]["id"], text="Thanks!")

    @app.action("feedback_down")
    def on_down(ack, body, action):
        ack()
        core.client.chat_postEphemeral(channel=body["channel"]["id"], user=body["user"]["id"],
                                       text="What was wrong?", blocks=reason_blocks(action["value"]))

    @app.action("feedback_reason")
    def on_reason(ack, body, action):
        ack()
        trace_id, reason = action["selected_option"]["value"].split("|", 1)
        core.record_feedback(body["user"]["id"], body["team"]["id"], trace_id, "down", reason)

    return app


def main() -> None:  # pragma: no cover - needs the owner's Slack app tokens
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    from analytics_agent.copilot.copilot import build_deployment

    bot_token, app_token = os.environ["SLACK_BOT_TOKEN"], os.environ["SLACK_APP_TOKEN"]
    client = WebClient(token=bot_token)
    team = client.auth_test()["team_id"]
    dep = build_deployment()
    dep.index.persist_purges = True
    core = SlackCopilot(Copilot(dep, purge_llm_on_delete=True), client, team)
    SocketModeHandler(build_app(core, bot_token, client), app_token).start()


if __name__ == "__main__":
    main()
