"""Fallback voice brain: our own agent loop on Groq's free tier.

Used only when the ElevenLabs agent is unavailable (e.g. credits exhausted). The browser does
speech-to-text and text-to-speech itself (free, built into Chrome/Edge) and sends each seller turn
here. This module runs the same system prompt and the same tools as the ElevenLabs agent:

    seller text -> LLM (Groq) -> tool calls run here -> streamed reply text + UI events -> browser

Design points
- Same brain as the main agent: SYSTEM_PROMPT and TOOLS come from agent_config, and the data tool
  runs the same deterministic diagnosis engine. Only the voice layer differs.
- Stateless server: the browser keeps the conversation history and sends it each turn, so this
  works on serverless hosting (Vercel) with no session storage.
- Rate limits: Groq's free tier allows 8,000 tokens/minute PER MODEL. On a 429 (or any error before
  output starts) we immediately try the next model, so each one's allowance adds up.
- Streaming: text is forwarded as it is generated, so the browser can start speaking the first
  sentence while the rest is still being written.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Iterator

import httpx

from app import agent_config as cfg
from app.diagnosis import agent_view, diagnose, load_seller_data

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Ordered by preference. Chosen by a latency/tool-use benchmark (docs/src/decision_log.md, D17).
MODELS: list[tuple[str, dict[str, Any]]] = [
    ("openai/gpt-oss-120b", {"reasoning_effort": "low"}),
    ("openai/gpt-oss-20b", {"reasoning_effort": "low"}),
    ("qwen/qwen3.8-27b", {"reasoning_effort": "none"}),
]
MAX_TOOL_ROUNDS = 4          # safety cap on tool -> LLM -> tool loops within one seller turn
MAX_RETRY_WAIT_SECS = 8      # if every model is rate-limited, wait at most this long once

def _fallback_prompt(prompt: str) -> str:
    """Same prompt as the main agent, minus the `show_on_screen` instructions.

    In this mode every tool call costs a full extra LLM round (slower reply, more tokens against
    the rate limit). The page already reveals each section by watching the agent's words, so the
    screen tool is dropped here and the keyword mechanism does the work.
    """
    start, end = prompt.index("# The seller's screen"), prompt.index("# Conversation plan")
    screen = ("# The seller's screen\n"
              "The seller's screen automatically shows the data for whatever you are talking about. "
              "Never mention the screen.\n\n")
    return prompt[:start] + screen + prompt[end:] + """

# Voice output (strict)
- Your words are read aloud by a text-to-speech voice. Plain spoken sentences only: no markdown,
  asterisks, bullet points, emojis or headings.
- HARD LIMIT: at most two sentences and about 35 words per reply. Never give two recommendations
  in one reply; give one, then stop and let the seller respond.
- Call `show_call_summary` only after you have given all the recommendations and the seller has
  agreed. Do not announce it."""


SYSTEM_PROMPT = _fallback_prompt(cfg.SYSTEM_PROMPT)

def _fn(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


TOOLS: dict[str, dict[str, Any]] = {t["name"]: _fn(t["name"], t["description"], t["parameters"])
                                    for t in cfg.TOOLS if t["name"] != "show_on_screen"}
TOOLS["end_call"] = _fn("end_call", "End the call after the seller has said goodbye and you have said goodbye too.",
                        {"type": "object", "properties": {}})


# ---------------------------------------------------------------- deterministic guard rails
# Smaller open models drift from a long prompt as the call goes on (recommending too early, ending
# the call by themselves). Instead of hoping the prompt holds, the conversation stage is computed
# in code from the history, and it decides (a) which tools the model is even offered and (b) a
# one-line "next step" hint. The model still chooses its words and adapts to the seller.
SUMMARY_AFTER_SELLER_TURNS = 5   # seller turns after the data is loaded before the summary tool is offered

_STEPS = [
    "Share the key signal (orders down, price unchanged so pricing is not the cause, out-of-stock products up) "
    "and ask ONE question about supply or restocking. Do not recommend anything yet.",
    "Ask ONE question about the rise in cancellations (were they cancelled by the seller because stock ran out?). "
    "Do not recommend anything yet.",
    "Explain in one or two sentences how the stock-outs connect the problems (fewer orders, more cancellations, "
    "likely lower visibility), then offer to go through the plan.",
    "Give recommendation 1 ONLY (action, why, rough impact, top products by name). Then stop.",
    "Give recommendation 2 ONLY. Then stop.",
    "Give recommendation 3 ONLY, then ask if the seller is comfortable with the plan.",
]


def _stage(msgs: list[dict[str, Any]]) -> tuple[bool, int, bool]:
    """(data loaded?, seller turns since the data was loaded, summary shown?)"""
    loaded, since, summary = False, 0, False
    for m in msgs:
        for tc in m.get("tool_calls") or []:
            name = tc["function"]["name"]
            loaded = loaded or name == "get_seller_diagnosis"
            summary = summary or name == "show_call_summary"
        if loaded and m["role"] == "user":
            since += 1
    return loaded, since, summary


def stage_tools(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    loaded, since, summary = _stage(msgs)
    if not loaded:
        return [TOOLS["get_seller_diagnosis"]]
    if summary:
        return [TOOLS["end_call"]]
    return [TOOLS["show_call_summary"]] if since >= SUMMARY_AFTER_SELLER_TURNS else []


def stage_hint(msgs: list[dict[str, Any]]) -> str | None:
    loaded, since, summary = _stage(msgs)
    if not loaded:
        return None
    if summary:
        step = ("Give a recap of two sentences at most, offer a follow-up check-in in seven days and ask if there is "
                "anything else. If the seller says they are done, say a warm goodbye and call end_call.")
    elif since < len(_STEPS):
        step = _STEPS[since]
    else:
        step = ("If the seller is comfortable with the plan, call show_call_summary now. Otherwise answer their "
                "concern briefly.")
    return ("Suggested next step. If the seller asked a question, disagreed or changed topic, answer that first in "
            "one or two sentences, then continue with this step: " + step)


class LLMUnavailable(Exception):
    """Every model failed (rate limits or errors)."""


# ---------------------------------------------------------------- tools (server side)
def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Runs a tool and returns the result the LLM sees. UI effects happen in the browser."""
    if name == "get_seller_diagnosis":
        return json.dumps(agent_view(diagnose(load_seller_data())), ensure_ascii=False)
    if name == "show_on_screen":
        return "Shown."
    if name == "show_call_summary":
        return "The summary is now displayed on the seller's screen."
    if name == "end_call":
        return "Call ended."
    return f"Unknown tool: {name}"


# ---------------------------------------------------------------- LLM streaming with model rotation
def _groq_key() -> str:
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not key:
        raise LLMUnavailable("GROQ_API_KEY is not configured")
    return key


def groq_stream(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Streams one completion. Yields {"text": delta} events, then {"tool_calls": [...]}.

    Tries each model in MODELS until one accepts the request. Falls through to the next model on
    rate limits or errors, as long as nothing has been streamed yet.
    """
    key = _groq_key()
    for attempt in range(2):
        retry_after = 0.0
        for model, extra in MODELS:
            body = {"model": model, "messages": messages, "temperature": cfg.TEMPERATURE,
                    "stream": True, "max_completion_tokens": 400, **extra}
            if tools:
                body["tools"] = tools
            try:
                with httpx.stream("POST", GROQ_URL, json=body, timeout=httpx.Timeout(30, connect=10),
                                  headers={"Authorization": f"Bearer {key}"}) as r:
                    if r.status_code != 200:
                        if r.status_code == 429:
                            retry_after = max(retry_after, float(r.headers.get("retry-after") or 2))
                        r.read()
                        continue  # try the next model
                    calls: dict[int, dict[str, str]] = {}
                    for line in r.iter_lines():
                        if not line.startswith("data: ") or line == "data: [DONE]":
                            continue
                        choices = json.loads(line[6:]).get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        if delta.get("content"):
                            yield {"text": delta["content"]}
                        for tc in delta.get("tool_calls") or []:
                            c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            c["id"] = tc.get("id") or c["id"]
                            fn = tc.get("function") or {}
                            c["name"] += fn.get("name") or ""
                            c["arguments"] += fn.get("arguments") or ""
                    yield {"tool_calls": [calls[i] for i in sorted(calls)], "model": model}
                    return
            except httpx.HTTPError:
                continue
        if attempt == 0 and 0 < retry_after <= MAX_RETRY_WAIT_SECS:
            time.sleep(retry_after)  # every model is rate-limited; wait once, then retry
            continue
        break
    raise LLMUnavailable("All models are busy or unavailable")


_SPEECH_FIXES = str.maketrans({"‑": "-", "‐": "-", "–": "-", "—": ", ", "’": "'",
                               "“": '"', "”": '"', "*": "", "#": "", " ": " ", " ": " "})


def _speakable(text: str) -> str:
    """Replaces typographic characters that text-to-speech voices read badly or skip."""
    return text.translate(_SPEECH_FIXES)


# ---------------------------------------------------------------- agent loop
def run_turn(history: list[dict[str, Any]],
             llm: Callable[..., Iterator[dict[str, Any]]] = groq_stream) -> Iterator[dict[str, Any]]:
    """Runs one seller turn. Yields events for the browser:

    {"type": "text", "delta": "..."}          spoken text, streamed
    {"type": "tool", "name": ..., "args": {}} a tool call (the browser applies its UI effect)
    {"type": "done", "messages": [...]}       new messages to append to the history
    {"type": "error", "message": "..."}       the LLM is unavailable
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    new: list[dict[str, Any]] = []
    try:
        spoke = False
        for _ in range(MAX_TOOL_ROUNDS):
            text, calls = "", []
            convo = history + new
            hint = stage_hint(convo)
            prompt = messages + new + ([{"role": "system", "content": hint}] if hint else [])
            asked = False  # a question ends the agent's turn; anything after it is dropped
            for ev in llm(prompt, stage_tools(convo)):
                if "text" in ev:
                    if asked:
                        continue
                    delta = _speakable(ev["text"])
                    if "?" in delta:
                        # Guard rail: some models answer their own question and keep talking.
                        delta, asked = delta[:delta.index("?") + 1], True
                    if spoke and not text:
                        yield {"type": "text", "delta": " "}  # keep rounds from running together
                    text += delta
                    spoke = True
                    yield {"type": "text", "delta": delta}
                elif "tool_calls" in ev:
                    calls = ev["tool_calls"]
            if not calls:
                new.append({"role": "assistant", "content": text})
                break
            new.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"] or f"call_{i}", "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for i, c in enumerate(calls)]})
            ended = False
            for i, c in enumerate(calls):
                try:
                    args = json.loads(c["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool", "name": c["name"], "args": args}
                new.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}",
                            "content": execute_tool(c["name"], args)})
                ended = ended or c["name"] == "end_call"
            if ended:
                break
    except LLMUnavailable as e:
        yield {"type": "error", "message": str(e)}
        return
    yield {"type": "done", "messages": new}
