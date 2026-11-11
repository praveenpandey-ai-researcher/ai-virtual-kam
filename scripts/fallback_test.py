"""Run a scripted seller conversation through the FALLBACK agent (Groq), in text.

Tests the same loop the browser uses in free mode, without speech. Turns are paced a few
seconds apart, like real speech, so the per-minute rate limits behave as they would on a call.

Usage:  python scripts/fallback_test.py [pace_seconds]
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from app import agent_config as cfg  # noqa: E402
from app import free_agent  # noqa: E402

TURNS = [
    "Hi Riya. My sales have dropped significantly this month. I don't understand why. Can you help me?",
    "Yes, my supplier delayed two shipments, so a lot of products ran out.",
    "Yes, mostly I had to cancel them myself because the stock wasn't there.",
    "Okay, that makes sense. What should I do?",
    "Sure, I can do that.",
    "Okay, makes sense.",
    "Yes, I'm comfortable with that plan.",
    "No, that's all. Thank you!",
]

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    pace = float(sys.argv[1]) if len(sys.argv) > 1 else 8
    history = [{"role": "assistant", "content": cfg.FIRST_MESSAGE}]
    print(f"RIYA: {cfg.FIRST_MESSAGE}")
    for turn in TURNS:
        time.sleep(pace)
        print(f"\nSELLER: {turn}")
        history.append({"role": "user", "content": turn})
        t0, first, text, ended = time.perf_counter(), None, "", False
        for ev in free_agent.run_turn(history):
            if ev["type"] == "text":
                first = first or time.perf_counter() - t0
                text += ev["delta"]
            elif ev["type"] == "tool":
                print(f"  [tool] {ev['name']} {json.dumps(ev['args'], ensure_ascii=False)[:400]}")
                ended = ended or ev["name"] == "end_call"
            elif ev["type"] == "done":
                history += ev["messages"]
            elif ev["type"] == "error":
                print("  ERROR:", ev["message"])
        print(f"RIYA ({first or 0:.1f}s to first word, {len(text.split())} words): {text}")
        if ended:
            print("  [call ended by agent]")
            break
