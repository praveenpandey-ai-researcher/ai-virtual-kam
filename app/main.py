"""FastAPI backend for the Mini AI Voice KAM.

Endpoints
  GET  /                 -> the voice call page
  GET  /api/diagnosis    -> seller metrics + computed diagnosis (drives the on-screen visuals)
  GET  /api/session      -> decides the voice for this call:
                              "elevenlabs" (primary): the first account in the chain that can take
                                 the call; returns a short-lived WebRTC token
                              "free" (fallback, when every account is exhausted): browser speech +
                                 our Groq agent loop
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
from app.accounts import elevenlabs_accounts, pick_account
from app.diagnosis import diagnose, load_seller_data

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / "static"
load_dotenv(ROOT / ".env")


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
@app.get("/api/session")
async def create_session(mode: Literal["auto", "elevenlabs", "free"] = "auto", after: int = -1) -> dict:
    """Picks the voice for this call.

    Order: ElevenLabs account 1, 2, 3, ... (the first that can take the call), and only when every
    account is unavailable, the free stack (browser speech + Groq).

    `after` is the position of an account that has just failed in the browser (it issued a token but
    dropped the call on connect, which is what an exhausted account does). The chain continues from
    the next one. The ElevenLabs agents are private: the browser only ever gets a short-lived token.
    """
    reasons: list[str] = ["requested"]
    if mode != "free":
        accounts = elevenlabs_accounts()
        account, token, reasons = await pick_account(accounts, after)
        if account and token:
            return {"mode": "elevenlabs", "conversation_token": token, "account": account.index,
                    "accounts": len(accounts), "skipped": reasons}
        if not accounts:
            reasons = ["ElevenLabs is not configured"]
        if mode == "elevenlabs":
            raise HTTPException(503, "; ".join(reasons) or "no ElevenLabs account available")
    if not _env("GROQ_API_KEY"):
        raise HTTPException(503, f"No voice service available: {'; '.join(reasons)}; fallback is not configured")
    return {"mode": "free", "reason": "; ".join(reasons), "first_message": cfg.FIRST_MESSAGE,
            "agent_name": cfg.KAM_NAME, "seller_name": cfg.SELLER_NAME}


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
    hindi_voice: bool = True  # False when the seller's browser has no Hindi voice: reply in Roman Hinglish


@app.post("/api/free/turn")
def free_turn(req: TurnRequest) -> StreamingResponse:
    """Runs one seller turn through the fallback agent and streams events as NDJSON lines."""
    history: list[dict[str, Any]] = [m.model_dump(exclude_none=True) for m in req.history]

    def events():
        for ev in free_agent.run_turn(history, req.hindi_voice):
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- fallback speech-to-text
MAX_AUDIO_BYTES = 4_000_000
AUDIO_EXT = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3"}
# Domain words help Whisper spell what sellers actually say.
WHISPER_PROMPTS = {
    "en": "A Snapdeal seller talking to their account manager about sales, orders, cancellations, stock, restocking, supplier, listings and prices.",
    "hi": "Snapdeal seller अपने account manager से बात कर रहा है: sales, orders, stock, cancellation, supplier, listing, price.",
}
# Counted in VOICED time (pauses excluded), so a normal sentence is ~2 s. Below this, language detection is
# unreliable ("yes", "okay", "हाँ") and the conversation's current language is used instead.
AUTO_DETECT_MIN_MS = 1200


def _lang_code(name: str | None) -> str:
    name = (name or "").strip().lower()
    return "en" if name.startswith("en") else "hi" if name.startswith("hi") else name


@app.post("/api/free/transcribe")
async def free_transcribe(request: Request) -> dict:
    """Transcribes one spoken seller turn with Whisper (Groq). The browser records it after detecting a pause.

    Language: English and Hindi/Hinglish are supported. Long enough clips are auto-detected; short ones
    use the conversation's current language (sent by the browser). Whisper often labels Hindi speech as
    Urdu or another language and writes it in the wrong script, so anything that is not English or
    Hindi is transcribed again as Hindi.
    """
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
    hint = request.headers.get("x-lang-hint", "en").lower()
    hint = hint if hint in WHISPER_PROMPTS else "en"
    try:
        voiced_ms = int(request.headers.get("x-voiced-ms", "0"))
    except ValueError:
        voiced_ms = 0

    last = None
    async with httpx.AsyncClient(timeout=30) as http:
        async def whisper(language: str | None) -> tuple[str, str] | None:
            nonlocal last
            data = {"temperature": "0", "response_format": "verbose_json"}
            if language:
                data.update(language=language, prompt=WHISPER_PROMPTS[language])
            for model in ("whisper-large-v3-turbo", "whisper-large-v3"):  # second model = separate rate limit
                last = await http.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                       headers={"Authorization": f"Bearer {key}"},
                                       files={"file": (f"speech.{AUDIO_EXT[content_type]}", audio, content_type)},
                                       data={"model": model, **data})
                if last.status_code == 200:
                    j = last.json()
                    return (j.get("text") or "").strip(), language or _lang_code(j.get("language"))
            return None

        result = await whisper(None if voiced_ms >= AUTO_DETECT_MIN_MS else hint)
        if result and result[1] not in WHISPER_PROMPTS:
            result = await whisper("hi")       # e.g. detected as Urdu: same speech, wrong script
        if result:
            return {"text": result[0], "language": result[1]}
    raise HTTPException(502, f"Transcription failed ({last.status_code if last is not None else 'no response'})")
