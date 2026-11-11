"""FastAPI backend for the Mini AI Voice KAM.

Endpoints
  GET /                 -> the voice call page
  GET /api/diagnosis    -> seller metrics + computed diagnosis (used by the agent's client tool)
  GET /api/session      -> short-lived WebRTC token for the private ElevenLabs agent
  GET /api/health       -> liveness check

Run:  uvicorn app.main:app --reload
"""
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.diagnosis import diagnose, load_seller_data

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / "static"
load_dotenv(ROOT / ".env")

app = FastAPI(title="Mini AI Voice KAM")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise HTTPException(500, f"{name} is not configured on the server")
    return value


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/diagnosis")
def get_diagnosis() -> dict:
    # Reloaded on every call so editing the JSON mock data needs no restart.
    return diagnose(load_seller_data())


@app.get("/api/session")
async def create_session() -> dict:
    """Exchange the server-side API key for a short-lived conversation token.

    The agent is private (auth enabled), so the browser never sees the API key and
    cannot start a call without going through this endpoint.
    """
    agent_id = _env("ELEVENLABS_AGENT_ID")
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(
            "https://api.elevenlabs.io/v1/convai/conversation/token",
            params={"agent_id": agent_id},
            headers={"xi-api-key": _env("ELEVENLABS_API_KEY")},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"ElevenLabs token request failed ({r.status_code})")
    return {"conversation_token": r.json()["token"]}
