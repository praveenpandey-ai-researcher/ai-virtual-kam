"""FastAPI backend for the Mini AI Voice KAM.

Endpoints
  GET  /                 -> the voice call page
  GET  /api/diagnosis    -> seller metrics + computed diagnosis (drives the on-screen visuals)
  GET  /api/session      -> decides the voice mode for this call:
                              "elevenlabs" (primary): returns a short-lived WebRTC token
                              "free" (fallback): browser speech + our Groq agent loop
  POST /api/free/turn    -> one seller turn in fallback mode, streamed as NDJSON events
  POST /api/free/transcribe -> speech-to-text for fallback mode (Whisper on Groq)
  GET  /api/health       -> liveness check

Run:  uvicorn app.main:app --port 8000
"""
import json
import os
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import agent_config as cfg
from app import free_agent
from app.diagnosis import diagnose, load_seller_data

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / "static"
load_dotenv(ROOT / ".env")

ELEVEN = "https://api.elevenlabs.io/v1"
MIN_ELEVENLABS_CREDITS = 300  # below this a call would die mid-sentence, so use the fallback instead

app = FastAPI(title="Mini AI Voice KAM")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


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


# ---------------------------------------------------------------- voice mode selection
async def _elevenlabs_token() -> tuple[str | None, str]:
    """Returns (token, reason). token is None when ElevenLabs cannot take a call right now."""
    key, agent_id = _env("ELEVENLABS_API_KEY"), _env("ELEVENLABS_AGENT_ID")
    if not key or not agent_id:
        return None, "ElevenLabs is not configured"
    headers = {"xi-api-key": key}
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            sub = await http.get(f"{ELEVEN}/user/subscription", headers=headers)
            if sub.status_code == 200:
                s = sub.json()
                remaining = (s.get("character_limit") or 0) - (s.get("character_count") or 0)
                if remaining < MIN_ELEVENLABS_CREDITS:
                    return None, f"ElevenLabs credits exhausted ({remaining} left)"
            # (if the key cannot read the subscription we still try for a token)
            tok = await http.get(f"{ELEVEN}/convai/conversation/token", params={"agent_id": agent_id}, headers=headers)
            if tok.status_code != 200:
                return None, f"ElevenLabs token request failed ({tok.status_code})"
            return tok.json()["token"], "ok"
    except httpx.HTTPError as e:
        return None, f"ElevenLabs unreachable ({type(e).__name__})"


@app.get("/api/session")
async def create_session(mode: Literal["auto", "elevenlabs", "free"] = "auto") -> dict:
    """Picks the voice mode. ElevenLabs is primary; the free stack is the automatic fallback.

    The ElevenLabs agent is private, so the browser never sees the API key: it gets a
    short-lived conversation token from here.
    """
    reason = "requested"
    if mode != "free":
        token, reason = await _elevenlabs_token()
        if token:
            return {"mode": "elevenlabs", "conversation_token": token}
        if mode == "elevenlabs":
            raise HTTPException(503, reason)
    if not _env("GROQ_API_KEY"):
        raise HTTPException(503, f"No voice service available: {reason}; fallback is not configured")
    return {"mode": "free", "reason": reason, "first_message": cfg.FIRST_MESSAGE, "agent_name": cfg.KAM_NAME}


# ---------------------------------------------------------------- fallback agent turn
class ToolFunction(BaseModel):
    name: str = Field(max_length=64)
    arguments: str = Field(max_length=4000)


class ToolCall(BaseModel):
    id: str = Field(max_length=80)
    type: Literal["function"] = "function"
    function: ToolFunction


class Message(BaseModel):
    role: Literal["user", "assistant", "tool"]  # the system prompt is added server-side, never by the client
    content: str | None = Field(default=None, max_length=8000)
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = Field(default=None, max_length=80)


class TurnRequest(BaseModel):
    history: list[Message] = Field(max_length=80)


@app.post("/api/free/turn")
def free_turn(req: TurnRequest) -> StreamingResponse:
    """Runs one seller turn through the fallback agent and streams events as NDJSON lines."""
    history: list[dict[str, Any]] = [m.model_dump(exclude_none=True) for m in req.history]

    def events():
        for ev in free_agent.run_turn(history):
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- fallback speech-to-text
MAX_AUDIO_BYTES = 4_000_000
AUDIO_EXT = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3"}
# Domain words help Whisper spell what sellers actually say.
WHISPER_PROMPT = "A Snapdeal seller talking to their account manager about sales, orders, cancellations, stock, restocking, supplier, listings and prices."


@app.post("/api/free/transcribe")
async def free_transcribe(request: Request) -> dict:
    """Transcribes one spoken seller turn with Whisper (Groq). The browser records it after detecting a pause."""
    key = _env("GROQ_API_KEY")
    if not key:
        raise HTTPException(503, "Transcription is not configured")
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in AUDIO_EXT:
        raise HTTPException(415, "Unsupported audio type")
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "Empty audio")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio too long")
    async with httpx.AsyncClient(timeout=30) as http:
        last = None
        for model in ("whisper-large-v3-turbo", "whisper-large-v3"):  # second model = separate rate limit
            last = await http.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (f"speech.{AUDIO_EXT[content_type]}", audio, content_type)},
                data={"model": model, "language": "en", "temperature": "0", "response_format": "json", "prompt": WHISPER_PROMPT},
            )
            if last.status_code == 200:
                return {"text": (last.json().get("text") or "").strip()}
    raise HTTPException(502, f"Transcription failed ({last.status_code if last is not None else 'no response'})")
