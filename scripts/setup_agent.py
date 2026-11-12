"""Create or update the ElevenLabs agent and its tools from app/agent_config.py, on EVERY account.

Usage:  python scripts/setup_agent.py

Accounts come from ELEVENLABS_API_KEYS in .env (comma-separated, in priority order). An agent belongs
to an account, so each account gets its own copy. The agent ids are saved to ELEVENLABS_AGENT_IDS,
aligned with the keys. Idempotent: tools are matched by name and updated in place; an agent is updated
if its id is already saved, otherwise created. An account that cannot be set up (for example its key
lacks the Agents permission) is reported and skipped; the others are still set up.
"""
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values, load_dotenv, set_key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)

from app import agent_config as cfg  # noqa: E402
from app.accounts import elevenlabs_accounts  # noqa: E402

API = "https://api.elevenlabs.io/v1/convai"


class SetupError(Exception):
    pass


def check(r: httpx.Response, what: str) -> dict:
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", {})
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = r.text[:300]
        raise SetupError(f"{what} failed ({r.status_code}): {message}")
    return r.json() if r.content else {}


def upsert_tools(http: httpx.Client) -> list[str]:
    existing = check(http.get(f"{API}/tools"), "list tools").get("tools", [])
    by_name = {t["tool_config"]["name"]: t["id"] for t in existing}
    ids = []
    for tool in cfg.TOOLS:
        body = {"tool_config": tool}
        if tool["name"] in by_name:
            tid = by_name[tool["name"]]
            check(http.patch(f"{API}/tools/{tid}", json=body), f"update tool {tool['name']}")
        else:
            tid = check(http.post(f"{API}/tools", json=body), f"create tool {tool['name']}")["id"]
        ids.append(tid)
    return ids


def agent_body(tool_ids: list[str]) -> dict:
    return {
        "name": cfg.AGENT_NAME,
        "conversation_config": {
            "agent": {
                "first_message": cfg.FIRST_MESSAGE,
                "language": "en",
                "prompt": {
                    "prompt": cfg.SYSTEM_PROMPT,
                    "llm": cfg.LLM,
                    "temperature": cfg.TEMPERATURE,
                    "tool_ids": tool_ids,
                    "built_in_tools": {
                        "end_call": {
                            "name": "end_call",
                            "description": "End the call after the seller has said goodbye.",
                            "params": {"system_tool_type": "end_call"},
                        }
                    },
                },
            },
            "tts": {"voice_id": cfg.VOICE_ID, "model_id": cfg.TTS_MODEL},
            "turn": {"turn_eagerness": "normal"},
        },
        # Private agent: the browser can only connect with a short-lived token issued by our backend.
        "platform_settings": {
            "auth": {"enable_auth": True},
            # Allows scripts/text_test.py to run the same agent in text-only mode (no TTS credits).
            "overrides": {"conversation_config_override": {"conversation": {"text_only": True}}},
        },
    }


def setup_account(key: str, agent_id: str) -> tuple[str, str]:
    """Sets up one account. Returns (agent id, what happened)."""
    with httpx.Client(headers={"xi-api-key": key}, timeout=30) as http:
        body = agent_body(upsert_tools(http))
        action = "updated"
        if agent_id and http.patch(f"{API}/agents/{agent_id}", json=body).status_code >= 400:
            agent_id = ""                      # saved id belongs to another account or was deleted
        if not agent_id:
            agent_id = check(http.post(f"{API}/agents/create", json=body), "create agent")["agent_id"]
            action = "created"
        a = check(http.get(f"{API}/agents/{agent_id}"), "get agent")   # read back what was stored
        p = a["conversation_config"]["agent"]["prompt"]
        return agent_id, (f"{action} {agent_id}  llm={p.get('llm')} tools={len(p.get('tool_ids') or [])} "
                          f"voice={a['conversation_config']['tts'].get('voice_id')}")


def main() -> None:
    accounts = elevenlabs_accounts()
    if not accounts:
        sys.exit("No ElevenLabs keys found. Set ELEVENLABS_API_KEYS (comma-separated) in .env")
    ids, ready = [], 0
    for acc in accounts:
        label = f"account {acc.index + 1} (key …{acc.key[-4:]})"
        try:
            agent_id, note = setup_account(acc.key, acc.agent_id)
            ids.append(agent_id); ready += 1
            print(f"  OK       {label}: {note}")
        except (SetupError, httpx.HTTPError) as e:
            ids.append(acc.agent_id)           # keep whatever was there; an empty id means "skip at runtime"
            print(f"  SKIPPED  {label}: {e}")
    set_key(str(ENV_FILE), "ELEVENLABS_AGENT_IDS", ",".join(ids), quote_mode="never")
    print(f"{ready} of {len(accounts)} accounts ready. Agent ids saved to .env (ELEVENLABS_AGENT_IDS).")


if __name__ == "__main__":
    main()
