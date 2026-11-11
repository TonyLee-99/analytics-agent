"""Directory service (Simulated identity provider) and Slack identity mapping.

The directory is event-sourced: every change is an event with a version and a timestamp, and the
state is whatever you get by applying the events in order. That lets the same code serve two roles:
- the *live* directory (source of truth, applies events immediately), and
- a *replica* (what the search index and the Postgres role table know), which only applies events
  older than its sync lag. The gap between the two is the revocation-lag window this project tests.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from analytics_agent.copilot.departments import DEPT_CODES
from analytics_agent.copilot.settings import CONFIG

ALL_DEPTS = frozenset(DEPT_CODES.values())


class IdentityError(Exception):
    """Raised when a caller cannot be mapped to exactly one active directory user (callers deny)."""


@dataclass
class DirState:
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)  # id -> {members:set, data:set, ...}
    version: int = 0

    def apply(self, ev: dict[str, Any]) -> None:
        t, p = ev["type"], ev["payload"]
        if t == "user_upsert":
            self.users[p["id"]] = {"id": p["id"], "name": p.get("name", p["id"]), "email": p["email"].lower(),
                                   "aliases": [a.lower() for a in p.get("aliases", [])],
                                   "title": p.get("title", ""), "active": p.get("active", True)}
        elif t == "group_upsert":
            g = self.groups.setdefault(p["id"], {"members": set(), "data": set(), "description": ""})
            g["description"] = p.get("description", g["description"])
            g["data"] = set(p.get("data", g["data"]))
        elif t == "member_add":
            self.groups.setdefault(p["group"], {"members": set(), "data": set(), "description": ""})
            self.groups[p["group"]]["members"].add(p["member"])
        elif t == "member_remove":
            if p["group"] in self.groups:
                self.groups[p["group"]]["members"].discard(p["member"])
        elif t == "user_deactivate":
            if p["id"] in self.users:
                self.users[p["id"]]["active"] = False
        elif t == "user_activate":
            if p["id"] in self.users:
                self.users[p["id"]]["active"] = True
        else:
            raise ValueError(f"unknown directory event {t}")
        self.version = ev["version"]

    # --- queries
    def effective_groups(self, user_id: str) -> frozenset[str]:
        """All groups the user belongs to, directly or through nested groups. Inactive -> none."""
        u = self.users.get(user_id)
        if not u or not u["active"]:
            return frozenset()
        # parents[x] = groups that list x as a member (x is a user id or a group id)
        parents: dict[str, set[str]] = {}
        for gid, g in self.groups.items():
            for m in g["members"]:
                parents.setdefault(m, set()).add(gid)
        out: set[str] = set()
        frontier = list(parents.get(user_id, ()))
        while frontier:  # BFS upward; `out` doubles as the visited set, so cycles terminate
            g = frontier.pop()
            if g in out:
                continue
            out.add(g)
            frontier.extend(parents.get(g, ()))
        return frozenset(out)

    def data_departments(self, user_id: str) -> frozenset[str]:
        depts: set[str] = set()
        for g in self.effective_groups(user_id):
            data = self.groups.get(g, {}).get("data", set())
            depts |= ALL_DEPTS if "*" in data else set(data)
        return frozenset(depts)

    def principals(self, user_id: str) -> frozenset[str]:
        """Identifiers a document ACL can name: the user id itself plus every effective group."""
        groups = self.effective_groups(user_id)
        return frozenset({f"user:{user_id}", *(f"group:{g}" for g in groups)}) if groups or self.is_active(
            user_id) else frozenset()

    def is_active(self, user_id: str) -> bool:
        u = self.users.get(user_id)
        return bool(u and u["active"])

    def fingerprint(self, user_id: str) -> str:
        blob = json.dumps({"active": self.is_active(user_id), "groups": sorted(self.effective_groups(user_id)),
                           "depts": sorted(self.data_departments(user_id))})
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def users_by_email(self, email: str) -> list[dict[str, Any]]:
        e = (email or "").strip().lower()
        return [u for u in self.users.values() if e and (u["email"] == e or e in u["aliases"])]


class Directory:
    """The live directory: an append-only event log plus the state it implies."""

    def __init__(self, clock: Callable[[], float] = time.time, log_path: Path | None = None):
        self.clock = clock
        self.events: list[dict[str, Any]] = []
        self.state = DirState()
        self.log_path = log_path
        self.listeners: list[Callable[[dict[str, Any]], None]] = []

    @classmethod
    def from_config(cls, path: Path = CONFIG / "org.yaml", clock: Callable[[], float] = time.time) -> Directory:
        cfg = yaml.safe_load(Path(path).read_text())
        d = cls(clock=clock)
        for gid, g in cfg["groups"].items():
            d.emit("group_upsert", {"id": gid, "description": g.get("description", ""), "data": g.get("data", [])})
        for u in cfg["users"]:
            d.emit("user_upsert", {k: u[k] for k in ("id", "name", "email", "aliases", "title", "active") if k in u})
            for g in u.get("groups", []):
                d.emit("member_add", {"group": g, "member": u["id"]})
        for gid, g in cfg["groups"].items():
            for m in g.get("members", []):
                d.emit("member_add", {"group": gid, "member": m})
        return d

    def emit(self, type_: str, payload: dict[str, Any]) -> dict[str, Any]:
        ev = {"version": len(self.events) + 1, "ts": self.clock(), "type": type_, "payload": payload}
        self.events.append(ev)
        self.state.apply(ev)
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(ev) + "\n")
        for fn in self.listeners:
            fn(ev)
        return ev

    # convenience mutators used by scenarios and tests
    def remove_member(self, group: str, member: str) -> dict[str, Any]:
        return self.emit("member_remove", {"group": group, "member": member})

    def add_member(self, group: str, member: str) -> dict[str, Any]:
        return self.emit("member_add", {"group": group, "member": member})

    def deactivate(self, user_id: str) -> dict[str, Any]:
        return self.emit("user_deactivate", {"id": user_id})


def apply_local_identity(d: Directory, path: Path) -> list[str]:
    """Dev-only override (config/local_identity.yaml): add a real email as an alias of a demo user.

    Refuses an email that already belongs to someone else, so it can never create an ambiguous mapping."""
    cfg = yaml.safe_load(Path(path).read_text()) or {}
    done = []
    for m in cfg.get("mappings", []):
        email, uid = str(m["slack_email"]).strip().lower(), m["as_user"]
        u = d.state.users.get(uid)
        if u is None:
            raise ValueError(f"local identity: unknown directory user {uid!r}")
        if any(x["id"] != uid for x in d.state.users_by_email(email)):
            raise ValueError(f"local identity: {email} already belongs to another directory user")
        d.emit("user_upsert", {**{k: u[k] for k in ("id", "name", "email", "title", "active")},
                               "aliases": sorted(set(u["aliases"]) | {email})})
        done.append(f"{email} -> {uid}")
    return done


class DirectoryReplica:
    """A lagged copy of the directory: applies only events older than `lag_s` when synced."""

    def __init__(self, source: Directory, lag_s: float = 0.0):
        self.source = source
        self.lag_s = lag_s
        self.state = DirState()
        self.applied = 0

    def sync(self, now: float | None = None) -> int:
        now = self.source.clock() if now is None else now
        n = 0
        while self.applied < len(self.source.events):
            ev = self.source.events[self.applied]
            if ev["ts"] > now - self.lag_s:
                break
            self.state.apply(ev)
            self.applied += 1
            n += 1
        return n

    def snapshot(self) -> DirState:
        return copy.deepcopy(self.state)


# ---------------------------------------------------------------- Slack identity mapping
@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    slack_user: str | None = None


def resolve_slack_user(slack_profile: dict[str, Any] | None, directory: DirState) -> Principal:
    """Map a Slack users.info payload to exactly one active directory user, or raise IdentityError.

    Default deny: anything unexpected (bot, deleted, no email, unknown, ambiguous, inactive) raises."""
    if not slack_profile:
        raise IdentityError("no Slack profile")
    if slack_profile.get("is_bot") or slack_profile.get("deleted") or slack_profile.get("id") == "USLACKBOT":
        raise IdentityError("bots and deleted Slack users are not mapped")
    if slack_profile.get("is_restricted") or slack_profile.get("is_ultra_restricted"):
        raise IdentityError("guest Slack accounts are not mapped")
    email = ((slack_profile.get("profile") or {}).get("email") or "").strip().lower()
    if not email:
        raise IdentityError("Slack profile has no email (missing users:read.email scope?)")
    matches = directory.users_by_email(email)
    if not matches:
        raise IdentityError("email not found in directory")
    if len(matches) > 1:
        raise IdentityError("email matches more than one directory user")
    u = matches[0]
    if not u["active"]:
        raise IdentityError("directory user is deactivated")
    return Principal(user_id=u["id"], email=u["email"], slack_user=slack_profile.get("id"))


def resolve_email(email: str, directory: DirState) -> Principal:
    """Same rules for callers identified by an SSO email (HTTP API, MCP)."""
    return resolve_slack_user({"id": None, "profile": {"email": email}}, directory)
