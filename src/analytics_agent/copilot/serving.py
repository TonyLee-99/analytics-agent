"""Copilot behind HTTP (FastAPI) and MCP, sharing one deployment.

Identity
- HTTP: the caller's email comes from `X-Forwarded-Email`, which only an SSO proxy may set. The proxy
  also sends `X-Identity-Signature = HMAC-SHA256(AA_PROXY_SECRET, email)`; without a valid signature
  the request is refused, so a client that reaches the app directly cannot claim an identity.
- MCP: the server acts for exactly one identity fixed at start-up (`AA_MCP_USER_EMAIL`), mapped through
  the directory like everyone else; tools never take an identity argument.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
from typing import Any

from analytics_agent.copilot.identity import IdentityError, resolve_email

_lock = threading.Lock()
_copilot = None


def get_copilot():
    global _copilot
    with _lock:
        if _copilot is None:
            from analytics_agent.copilot.copilot import Copilot, build_deployment

            _copilot = Copilot(build_deployment())
        return _copilot


def set_copilot(cp) -> None:  # tests inject a small deployment
    global _copilot
    _copilot = cp


def sign(email: str, secret: str | None = None) -> str:
    key = (secret or os.environ.get("AA_PROXY_SECRET", "")).encode()
    return hmac.new(key, email.strip().lower().encode(), hashlib.sha256).hexdigest()


def verify_proxy_identity(email: str | None, signature: str | None) -> str:
    secret = os.environ.get("AA_PROXY_SECRET", "")
    if not secret:
        raise IdentityError("proxy secret not configured; refusing header identity")
    if not email or not signature or not hmac.compare_digest(sign(email, secret), signature):
        raise IdentityError("missing or invalid identity signature")
    return email


def ask_as_email(email: str, question: str) -> dict[str, Any]:
    cp = get_copilot()
    p = resolve_email(email, cp.dep.directory.state)
    a = cp.ask(p.user_id, question)
    d = a.to_dict()
    d["user_id"] = p.user_id
    return d


def search_as_email(email: str, query: str, k: int = 5) -> list[dict[str, Any]]:
    cp = get_copilot()
    p = resolve_email(email, cp.dep.directory.state)
    res = cp.dep.retriever.search(p.user_id, query, k=k)
    return [{"doc_id": h.chunk.doc_id, "title": h.chunk.title, "url": h.chunk.url, "span": [h.chunk.start, h.chunk.end],
             "text": h.chunk.text[:600]} for h in res.hits]


def list_metrics() -> list[dict[str, Any]]:
    from analytics_agent.copilot.copilot import LAYER_PATH
    from analytics_agent.semantic.compiler import load_layer

    layer = load_layer(LAYER_PATH)
    return [{"name": n, "label": m["label"], "description": m["description"], "family": m.get("family"),
             "owner": m.get("owner")} for n, m in layer.metrics.items()]
