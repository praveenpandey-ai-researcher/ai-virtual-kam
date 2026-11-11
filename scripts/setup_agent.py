"""Create or update the ElevenLabs agent and its tools from app/agent_config.py.

Usage:  python scripts/setup_agent.py

Idempotent: tools are matched by name and updated in place; the agent is updated if
ELEVENLABS_AGENT_ID is already set in .env, otherwise created and its id saved to .env.
"""
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import agent_config as cfg  # noqa: E402

ENV_FILE = ROOT / ".env"
API = "https://api.elevenlabs.io/v1/convai"


def client() -> httpx.Client:
    key = (dotenv_values(ENV_FILE).get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        sys.exit("ELEVENLABS_API_KEY missing in .env")
    return httpx.Client(headers={"xi-api-key": key}, timeout=30)


def check(r: httpx.Response, what: str) -> dict:
    if r.status_code >= 400:
        sys.exit(f"{what} failed ({r.status_code}): {r.text[:1500]}")
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
            print(f"  updated tool  {tool['name']}  ({tid})")
        else:
            tid = check(http.post(f"{API}/tools", json=body), f"create tool {tool['name']}")["id"]
            print(f"  created tool  {tool['name']}  ({tid})")
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


def main() -> None:
    with client() as http:
        print("Tools:")
        tool_ids = upsert_tools(http)
        body = agent_body(tool_ids)
        agent_id = (dotenv_values(ENV_FILE).get("ELEVENLABS_AGENT_ID") or "").strip()
        if agent_id:
            check(http.patch(f"{API}/agents/{agent_id}", json=body), "update agent")
            print(f"Agent updated: {agent_id}")
        else:
            agent_id = check(http.post(f"{API}/agents/create", json=body), "create agent")["agent_id"]
            set_key(str(ENV_FILE), "ELEVENLABS_AGENT_ID", agent_id, quote_mode="never")
            print(f"Agent created: {agent_id} (saved to .env)")

        # Read back what ElevenLabs actually stored, as a sanity check.
        a = check(http.get(f"{API}/agents/{agent_id}"), "get agent")
        p = a["conversation_config"]["agent"]["prompt"]
        print(f"  llm={p.get('llm')}  tools={len(p.get('tool_ids') or [])}  "
              f"voice={a['conversation_config']['tts'].get('voice_id')}  "
              f"tts_model={a['conversation_config']['tts'].get('model_id')}  "
              f"auth={a.get('platform_settings', {}).get('auth', {}).get('enable_auth')}")


if __name__ == "__main__":
    main()
