"""D1: the Bolt app, unchanged, against the local Slack emulator."""

import json

import pytest
from slack_bolt.request import BoltRequest
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from analytics_agent.copilot.slack_app import SlackCopilot, build_app
from analytics_agent.copilot.slack_emulator import SlackEmulator

from .helpers import small_copilot


@pytest.fixture()
def bot(tmp_path):
    emu = SlackEmulator()
    base = emu.start()
    cp = small_copilot(tmp_path)
    for uid, u in cp.dep.directory.state.users.items():
        emu.add_user(f"U{uid.upper()}", u["email"], u["name"])
    emu.add_user("UNOMAIL", None, "No Email")
    client = WebClient(token="xoxb-test", base_url=base)
    core = SlackCopilot(cp, client, emu.team_id, feedback_path=tmp_path / "feedback.jsonl")
    app = build_app(core, "xoxb-test", client, testing=True)
    yield emu, core, app, tmp_path
    emu.stop()


def send(app, emu, event, event_id):
    return app.dispatch(BoltRequest(body=emu.event_body(event, event_id), mode="socket_mode"))


def dm(user, text, ts="1.1"):
    return {"type": "message", "channel_type": "im", "channel": "D0" + user, "user": user, "text": text, "ts": ts}


def test_mention_answers_with_citations_and_feedback_buttons(bot):
    emu, core, app, _ = bot
    resp = send(app, emu, dm("UMARIA", "how are potholes fixed?"), "Ev1")
    assert resp.status == 200
    msg = [m for m in emu.posted if m["kind"] == "message"][-1]
    assert "[S1]" in msg["text"]
    kinds = [b["type"] for b in msg["blocks"]]
    assert "actions" in kinds and any("example.test" in json.dumps(b) for b in msg["blocks"])


def test_redelivered_event_is_answered_once(bot):
    emu, core, app, _ = bot
    send(app, emu, dm("UMARIA", "how are potholes fixed?"), "EvSame")
    send(app, emu, dm("UMARIA", "how are potholes fixed?"), "EvSame")  # Slack retry
    assert len([m for m in emu.posted if m["kind"] == "message"]) == 1


def test_unmapped_identity_is_denied(bot):
    emu, core, app, _ = bot
    send(app, emu, dm("UNOMAIL", "how are potholes fixed?"), "Ev2")
    assert [m["kind"] for m in emu.posted] == ["ephemeral"]
    assert "can't verify" in emu.posted[0]["text"]
    assert core.log[-1]["delivery"] == "denied"


def test_other_workspace_is_denied(bot):
    emu, core, app, _ = bot
    r = core.handle_message("T0OTHER", dm("UMARIA", "potholes?"), "Ev3")
    assert r["delivery"] == "denied"


def test_restricted_answer_goes_ephemeral_in_mixed_channel(bot):
    emu, core, app, _ = bot
    emu.channels["C1"] = ["ULENA", "UMARIA", "UDEV"]  # dev is not entitled to the CDOT doc
    ev = {"type": "app_mention", "channel": "C1", "user": "ULENA", "text": "<@U0BOT> pothole crews target", "ts": "2.1"}
    send(app, emu, ev, "Ev4")
    rec = core.log[-1]
    assert "cdot-1" in rec["answer"].doc_deps
    assert rec["delivery"] == "ephemeral" and emu.posted[-1]["kind"] == "ephemeral"


def test_public_when_whole_channel_is_entitled(bot):
    emu, core, app, _ = bot
    emu.channels["C2"] = ["ULENA", "UMARIA"]
    ev = {"type": "app_mention", "channel": "C2", "user": "UMARIA", "text": "<@U0BOT> pothole crews target", "ts": "3.1"}
    send(app, emu, ev, "Ev5")
    assert core.log[-1]["delivery"] == "public"


def test_feedback_up_and_down_with_reason(bot):
    emu, core, app, tmp = bot
    send(app, emu, dm("UMARIA", "how are potholes fixed?"), "Ev6")
    trace_id = core.log[-1]["answer"].trace_id
    ts = core.log[-1]["ts"]
    app.dispatch(BoltRequest(body=emu.action_body("UMARIA", "D0UMARIA", "feedback_up", trace_id, ts), mode="socket_mode"))
    app.dispatch(BoltRequest(body=emu.action_body("UMARIA", "D0UMARIA", "feedback_down", trace_id, ts), mode="socket_mode"))
    assert any(b.get("type") == "actions" for b in emu.posted[-1]["blocks"])  # reason picker
    app.dispatch(BoltRequest(body=emu.action_body("UMARIA", "D0UMARIA", "feedback_reason", "", ts,
                                                  selected=f"{trace_id}|wrong_number"), mode="socket_mode"))
    rows = [json.loads(x) for x in (tmp / "feedback.jsonl").read_text().splitlines()]
    assert [(r["vote"], r["reason"], r["trace_id"], r["user_id"]) for r in rows] == [
        ("up", None, trace_id, "maria"), ("down", "wrong_number", trace_id, "maria")]


def test_web_api_fault_surfaces_without_posting_twice(bot):
    emu, core, app, _ = bot
    emu.fail_next["chat.postMessage"] = "channel_not_found"
    with pytest.raises(SlackApiError):
        core.handle_message(emu.team_id, dm("UMARIA", "potholes?"), "Ev7")
    assert not [m for m in emu.posted if m["kind"] == "message"]


def test_audience_is_enumerated_across_pages(bot):
    emu, core, app, _ = bot
    emu.page_size = 1  # the unentitled member is only on the last page
    emu.channels["C3"] = ["ULENA", "UMARIA", "UDEV"]
    ev = {"type": "app_mention", "channel": "C3", "user": "ULENA", "text": "<@U0BOT> pothole crews target", "ts": "4.1"}
    send(app, emu, ev, "Ev8")
    assert core.log[-1]["delivery"] == "ephemeral" and None not in core.log[-1]["audience"]


def test_audience_lookup_failure_fails_closed(bot):
    emu, core, app, _ = bot
    emu.channels["C4"] = ["ULENA", "UMARIA"]
    emu.fail_next["conversations.members"] = "internal_error"
    ev = {"type": "app_mention", "channel": "C4", "user": "UMARIA", "text": "<@U0BOT> pothole crews target", "ts": "5.1"}
    send(app, emu, ev, "Ev9")
    assert core.log[-1]["delivery"] == "ephemeral"


def test_revocation_during_audience_lookup_discards(bot):
    emu, core, app, _ = bot
    emu.channels["C5"] = ["UMARIA"]
    orig = core.channel_audience

    def revoke_then_lookup(team, ch):
        core.copilot.dep.directory.remove_member("dept-cdot", "maria")
        return orig(team, ch)
    core.channel_audience = revoke_then_lookup
    ev = {"type": "app_mention", "channel": "C5", "user": "UMARIA", "text": "<@U0BOT> pothole crews target", "ts": "6.1"}
    send(app, emu, ev, "Ev10")
    assert core.log[-1]["delivery"] == "discarded"
    assert all("crews" not in m["text"] for m in emu.posted)
