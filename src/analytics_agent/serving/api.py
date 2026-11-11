"""HTTP API.   uv run uvicorn analytics_agent.serving.api:app --port 8000

POST /ask        {"question": "...", "db_id": "olist"} -> SQL, rows, summary, confidence, clarification
GET  /tables     ?db_id=olist                          -> catalog (with PII flags)
GET  /health
GET  /           minimal chat UI
POST /v2/ask     {"question": "..."} + SSO proxy headers -> copilot answer (docs + warehouse, permission-aware)
GET  /v2/metrics governed 311 metric catalog
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from analytics_agent.serving import service

app = FastAPI(title="analytics-agent", version="0.1.0")
STATIC = Path(__file__).parent / "static"


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    db_id: str = "olist"
    n_samples: int | None = Field(default=None, ge=1, le=8, description="self-consistency samples (default 5)")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/ask")
def ask(req: AskRequest) -> dict:
    try:
        return service.ask(req.question, req.db_id, req.n_samples)
    except FileNotFoundError as e:
        raise HTTPException(404, f"database not available: {e}") from e


@app.get("/tables")
def tables(db_id: str = "olist") -> list[dict]:
    try:
        return service.list_tables(db_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, str(e)) from e


class CopilotRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


@app.post("/v2/ask")
def ask_v2(req: CopilotRequest, x_forwarded_email: str | None = Header(default=None),
           x_identity_signature: str | None = Header(default=None)) -> dict:
    from analytics_agent.copilot import serving as v2
    from analytics_agent.copilot.identity import IdentityError

    try:
        email = v2.verify_proxy_identity(x_forwarded_email, x_identity_signature)
        return v2.ask_as_email(email, req.question)
    except IdentityError as e:
        raise HTTPException(403, f"identity not accepted: {e}") from e


@app.get("/v2/metrics")
def metrics_v2() -> list[dict]:
    from analytics_agent.copilot import serving as v2

    return v2.list_metrics()


@app.get("/")
def ui() -> FileResponse:
    return FileResponse(STATIC / "index.html")
