"""Local Slack emulator: the Web API methods the bot uses, plus an event-envelope driver.

The Bolt app talks to this server through the real `slack_sdk.WebClient` (only `base_url` differs), so
the code path under test is the production one. Differences from real Slack (documented in
docs/RUNBOOK.md): no OAuth or signing (Socket Mode needs neither), no rate-limit tiers beyond injected
faults, `conversations.members` paginates by a simple offset cursor, no Block Kit validation.

Methods: auth.test, users.info, conversations.members, chat.postMessage, chat.postEphemeral, chat.update.
Fault injection: `emu.fail_next["chat.postMessage"] = "ratelimited"` (HTTP 429 + Retry-After: 1) or any
Slack error string (HTTP 200, ok=false).
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs


class SlackEmulator:
    def __init__(self, team_id: str = "T0CITY", bot_user: str = "U0BOT"):
        self.team_id = team_id
        self.bot_user = bot_user
        self.users: dict[str, dict[str, Any]] = {}
        self.channels: dict[str, list[str]] = {}
        self.posted: list[dict[str, Any]] = []  # every message / ephemeral / update, in order
        self.calls: list[tuple[str, dict]] = []
        self.fail_next: dict[str, str] = {}
        self.page_size = 200  # conversations.members page size (tests shrink it to exercise pagination)
        self._ts = itertools.count(1)
        self._server: ThreadingHTTPServer | None = None

    # --- directory of Slack users
    def add_user(self, user_id: str, email: str | None, name: str = "", **flags: Any) -> dict[str, Any]:
        u = {"id": user_id, "team_id": self.team_id, "name": name or user_id, "real_name": name, "deleted": False,
             "is_bot": False, "is_restricted": False, "is_ultra_restricted": False,
             "profile": {"display_name": name, "real_name": name, **({"email": email} if email else {})}}
        u.update(flags)
        self.users[user_id] = u
        return u

    # --- Web API
    def handle(self, method: str, args: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, str]]:
        self.calls.append((method, args))
        fault = self.fail_next.pop(method, None)
        if fault == "ratelimited":
            return 429, {"ok": False, "error": "ratelimited"}, {"Retry-After": "1"}
        if fault:
            return 200, {"ok": False, "error": fault}, {}
        if method == "auth.test":
            return 200, {"ok": True, "team_id": self.team_id, "user_id": self.bot_user, "bot_id": "B0BOT"}, {}
        if method == "users.info":
            u = self.users.get(args.get("user", ""))
            return (200, {"ok": True, "user": u}, {}) if u else (200, {"ok": False, "error": "user_not_found"}, {})
        if method == "conversations.members":
            ch = args.get("channel", "")
            if ch not in self.channels:
                return 200, {"ok": False, "error": "channel_not_found"}, {}
            members = list(self.channels[ch])
            start = int(args.get("cursor") or 0)
            page = members[start:start + self.page_size]
            nxt = str(start + self.page_size) if start + self.page_size < len(members) else ""
            return 200, {"ok": True, "members": page, "response_metadata": {"next_cursor": nxt}}, {}
        if method in ("chat.postMessage", "chat.postEphemeral"):
            ts = f"{int(time.time())}.{next(self._ts):06d}"
            msg = {"kind": "ephemeral" if method == "chat.postEphemeral" else "message", "ts": ts,
                   "channel": args.get("channel"), "user": args.get("user"), "thread_ts": args.get("thread_ts"),
                   "text": args.get("text", ""), "blocks": _maybe_json(args.get("blocks"))}
            self.posted.append(msg)
            out = {"ok": True, "channel": args.get("channel"), "ts": ts}
            if method == "chat.postEphemeral":
                out = {"ok": True, "message_ts": ts}
            return 200, out, {}
        if method == "chat.update":
            self.posted.append({"kind": "update", "ts": args.get("ts"), "channel": args.get("channel"),
                                "text": args.get("text", ""), "blocks": _maybe_json(args.get("blocks"))})
            return 200, {"ok": True, "channel": args.get("channel"), "ts": args.get("ts")}, {}
        return 200, {"ok": False, "error": "unknown_method"}, {}

    # --- HTTP server (so slack_sdk.WebClient can talk to it)
    def start(self) -> str:
        emu = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server API
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n).decode()
                ctype = self.headers.get("Content-Type", "")
                if "json" in ctype:
                    args = json.loads(raw or "{}")
                else:
                    args = {k: v[0] for k, v in parse_qs(raw).items()}
                method = self.path.rsplit("/", 1)[-1]
                status, body, headers = emu.handle(method, args)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):  # silence
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}/api/"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    # --- event envelopes (what Socket Mode would push to the app)
    def event_body(self, event: dict[str, Any], event_id: str) -> dict[str, Any]:
        return {"token": "emulator", "team_id": self.team_id, "api_app_id": "A0COPILOT", "type": "event_callback",
                "event_id": event_id, "event_time": int(time.time()), "event": event,
                "authorizations": [{"team_id": self.team_id, "user_id": self.bot_user, "is_bot": True}]}

    def action_body(self, user_id: str, channel: str, action_id: str, value: str, message_ts: str,
                    selected: str | None = None) -> dict[str, Any]:
        action = {"action_id": action_id, "block_id": "feedback", "type": "button", "value": value}
        if selected is not None:
            action = {"action_id": action_id, "block_id": "feedback", "type": "static_select",
                      "selected_option": {"value": selected, "text": {"type": "plain_text", "text": selected}}}
        return {"type": "block_actions", "team": {"id": self.team_id}, "user": {"id": user_id, "team_id": self.team_id},
                "api_app_id": "A0COPILOT", "channel": {"id": channel}, "container": {"message_ts": message_ts},
                "message": {"ts": message_ts}, "actions": [action], "trigger_id": "trig"}


def _maybe_json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v
