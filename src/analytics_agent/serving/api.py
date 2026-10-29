"""HTTP API.   uv run uvicorn analytics_agent.serving.api:app --port 8000

POST /ask        {"question": "...", "db_id": "olist"} -> SQL, rows, summary, confidence, clarification
GET  /tables     ?db_id=olist                          -> catalog (with PII flags)
GET  /health
GET  /           minimal chat UI
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
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


@app.get("/")
def ui() -> FileResponse:
    return FileResponse(STATIC / "index.html")
