"""Run a scripted seller conversation against the live agent in TEXT-ONLY mode.

Same agent, prompt, LLM and tools as the voice call, but no speech synthesis, so it is
cheap to iterate on the prompt. Client tools are answered with the real diagnosis engine.

Usage:  python scripts/text_test.py [scenario]      scenarios: happy (default), pushback, price
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import websockets
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.accounts import elevenlabs_accounts  # noqa: E402
from app.diagnosis import diagnose, load_seller_data  # noqa: E402

SCENARIOS = {
    "happy": [
        "Hi Riya. My sales have dropped significantly this month. I don't understand why. Can you help me?",
        "Yes, actually my supplier delayed two shipments, so a lot of products ran out.",
        "Yes, mostly I had to cancel them myself because the stock wasn't there.",
        "Okay, that makes sense. What should I do?",
        "That sounds good, I can do that.",
        "No, that's all. Thank you!",
    ],
    "pushback": [
        "My sales dropped a lot this month, why is that?",
        "No, my stock is fine, I don't think that's the problem.",
        "Hmm, maybe the cancellations are from customers changing their minds.",
        "Okay, so what do you suggest?",
        "Thanks, bye.",
    ],
    "price": [
        "Sales are down this month. Should I just reduce my prices to get more orders?",
        "Okay but my competitor is running discounts, so I feel I should too.",
        "Alright, what else should I do then?",
        "Thanks, that's all.",
    ],
}


def answer_tool(name: str, params: dict) -> str:
    if name == "get_seller_diagnosis":
        return json.dumps(diagnose(load_seller_data()), ensure_ascii=False)
    if name == "show_call_summary":
        print("\n  [SUMMARY CARD]\n  " + json.dumps(params, indent=2, ensure_ascii=False).replace("\n", "\n  "))
        return "The summary is now displayed on the seller's screen."
    if name == "show_on_screen":
        return "Shown."
    return f"Unknown tool {name}"


async def run(turns: list[str]) -> None:
    load_dotenv(ROOT / ".env")
    acc = next((a for a in elevenlabs_accounts() if a.agent_id), None)
    if not acc:
        sys.exit("No ElevenLabs account with an agent. Run scripts/setup_agent.py first.")
    print(f"(using account {acc.index + 1})")
    r = httpx.get("https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                  params={"agent_id": acc.agent_id}, headers={"xi-api-key": acc.key}, timeout=20)
    r.raise_for_status()

    async with websockets.connect(r.json()["signed_url"], max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {"conversation": {"text_only": True}},
        }))

        async def drain(quiet_secs: float = 10.0) -> bool:
            """Read events until the agent goes quiet. Returns False if the call ended.

            Quiet = no agent response or tool call for `quiet_secs`; keep-alive pings don't count.
            """
            loop = asyncio.get_running_loop()
            deadline = loop.time() + quiet_secs
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return True
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    return True
                except websockets.ConnectionClosed:
                    print("  [call ended by agent]")
                    return False
                ev = json.loads(raw)
                t = ev.get("type")
                if t in ("agent_response", "client_tool_call"):
                    deadline = loop.time() + quiet_secs
                if t == "ping":
                    await ws.send(json.dumps({"type": "pong", "event_id": ev["ping_event"]["event_id"]}))
                elif t == "agent_response":
                    print(f"\nRIYA:   {ev['agent_response_event']['agent_response']}")
                elif t == "client_tool_call":
                    c = ev["client_tool_call"]
                    print(f"  [tool call] {c['tool_name']}({json.dumps(c.get('parameters', {}))[:120]})")
                    await ws.send(json.dumps({"type": "client_tool_result", "tool_call_id": c["tool_call_id"],
                                              "result": answer_tool(c["tool_name"], c.get("parameters", {})),
                                              "is_error": False}))

        if not await drain():
            return
        for text in turns:
            print(f"\nSELLER: {text}")
            await ws.send(json.dumps({"type": "user_message", "text": text}))
            if not await drain():
                return


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "happy"
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"=== scenario: {name} ===")
    asyncio.run(run(SCENARIOS[name]))
