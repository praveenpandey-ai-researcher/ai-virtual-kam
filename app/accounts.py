"""ElevenLabs account chain.

The primary voice runs on ElevenLabs. Several accounts can be configured; a call uses the first
one that can take it, and moves down the list when one is out of credits. Only when every account
is unavailable does the app fall back to the free stack (browser speech + Groq).

Configuration (.env / hosting environment), comma-separated and aligned by position:
    ELEVENLABS_API_KEYS=key1,key2,key3
    ELEVENLABS_AGENT_IDS=agent1,agent2,agent3     (written by scripts/setup_agent.py)

An agent belongs to an account, not to a key, so every account has its own copy of the agent.
An empty agent id means "not set up yet" and that account is skipped.
The single-account variables ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID still work.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Awaitable, Callable, NamedTuple

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

ELEVEN = "https://api.elevenlabs.io/v1"
MIN_CREDITS = 300  # below this a call would die mid-sentence, so move on to the next account


class Account(NamedTuple):
    index: int      # position in the chain (0 = first choice)
    key: str
    agent_id: str


def _split(name: str) -> list[str]:
    return [part.strip() for part in (os.getenv(name) or "").split(",")]


def elevenlabs_accounts() -> list[Account]:
    """All configured accounts in priority order. Accounts without an agent keep their slot (index)."""
    keys = [k for k in _split("ELEVENLABS_API_KEYS") if k]
    if keys:
        ids = _split("ELEVENLABS_AGENT_IDS")
        return [Account(i, k, ids[i] if i < len(ids) else "") for i, k in enumerate(keys)]
    key, agent = (os.getenv("ELEVENLABS_API_KEY") or "").strip(), (os.getenv("ELEVENLABS_AGENT_ID") or "").strip()
    return [Account(0, key, agent)] if key else []


async def probe(account: Account) -> tuple[str | None, str]:
    """Asks one account for a call token. Returns (token, reason); token is None if it cannot take a call."""
    if not account.agent_id:
        return None, "agent not set up"
    headers = {"xi-api-key": account.key}
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            sub = await http.get(f"{ELEVEN}/user/subscription", headers=headers)
            if sub.status_code == 200:
                s = sub.json()
                remaining = (s.get("character_limit") or 0) - (s.get("character_count") or 0)
                if remaining < MIN_CREDITS:
                    return None, f"credits exhausted ({remaining} left)"
            # A key without permission to read the balance is still tried. If it turns out to be
            # exhausted, the call drops on connect and the browser asks for the next account.
            tok = await http.get(f"{ELEVEN}/convai/conversation/token", params={"agent_id": account.agent_id}, headers=headers)
            if tok.status_code != 200:
                return None, f"token refused ({tok.status_code})"
            token = tok.json().get("token")
            if not token:
                return None, "empty token returned"
            return token, "ok"
    except httpx.HTTPError as e:
        return None, f"unreachable ({type(e).__name__})"


async def pick_account(accounts: list[Account], after: int = -1,
                       probe_fn: Callable[[Account], Awaitable[tuple[str | None, str]]] = probe,
                       ) -> tuple[Account | None, str | None, list[str]]:
    """Walks the chain in order, starting after position `after`, and returns the first account that
    issues a token: (account, token, reasons for every account that was skipped)."""
    skipped: list[str] = []
    for account in accounts:
        if account.index <= after:
            continue
        token, reason = await probe_fn(account)
        if token:
            return account, token, skipped
        skipped.append(f"account {account.index + 1}: {reason}")
    return None, None, skipped
