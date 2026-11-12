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
import re
import time
from typing import Any, Callable, Iterator

import httpx

from app import agent_config as cfg
from app.diagnosis import agent_view, diagnose, load_seller_data, spoken_inr

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Ordered by preference. Chosen by a latency/tool-use benchmark (docs/src/decision_log.md, D17).
MODELS: list[tuple[str, dict[str, Any]]] = [
    ("openai/gpt-oss-120b", {"reasoning_effort": "low"}),
    ("openai/gpt-oss-20b", {"reasoning_effort": "low"}),
    ("qwen/qwen3.8-27b", {"reasoning_effort": "none"}),
]
FALLBACK_TEMPERATURE = 0.6   # natural, varied phrasing; facts stay fixed because they come from the tool
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
- Keep replies conversational: usually one to three sentences. Give one recommendation per
  reply, then stop and let the seller respond. Explain at more length only when they ask.
- Call `show_call_summary` only after you have given all the recommendations and the seller has
  agreed. Do not announce it.
- Once you have the account data, you have it for the whole call. Never say again that you are
  pulling up, checking or confirming the account.

# Language: match the seller
- Reply in the language of the seller's most recent message. English gets English.
- If the seller speaks Hindi or Hinglish (in Devanagari or in Roman letters), reply in natural,
  friendly Hinglish, the way an Indian account manager really talks: Hindi sentences with the
  business words kept in English (sales, orders, stock, out of stock, restock, cancellation,
  listing, price, promotion, visibility, best-seller) and product names in English.
- You are a woman: in Hindi always use feminine verb forms for yourself ("मैं देखती हूँ",
  "समझ गई", "बताती हूँ", "करूँगी"), never masculine ones ("देखता हूँ", "समझ गया", "करूँगा").
- Keep it everyday and simple, not formal or bookish Hindi. Say "cancellation" not "रद्दीकरण",
  "supply" not "आपूर्ति", "problem" not "समस्या", "sales" not "बिक्री". Example of the right tone:
  "आपके orders कम हुए हैं क्योंकि काफ़ी products out of stock हो गए थे।"
- In Hindi, address the seller politely with "जी" and "आप", and say numbers in Hindi words
  (for example "एक लाख पैंतीस हज़ार रुपये", "चौदह प्रतिशत").
- Switch language whenever the seller switches. The summary shown on screen stays in English."""


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
SUMMARY_AFTER_SELLER_TURNS = 12  # safety net: offer the summary tool by now even if a recommendation was not detected
MAX_SENTENCES = 4                # hard cap on one spoken reply...
MAX_SENTENCES_EXPLAIN = 7        # ...unless the seller asked for an explanation
# The seller asked a question or asked to hear more: a longer answer is welcome.
_WANTS_DETAIL = re.compile(
    r"\?|\b(explain|detail|elaborate|tell me (more|about)|how (do|does|can|should|to)|why|what (is|are|about|if)|"
    r"which|difference|meaning|example)\b|समझा|विस्तार|कैसे|क्यों|क्या है|बताइए|बताओ|बताइये|मतलब|"
    r"\b(samjha|kaise|kyun|kyon|batao|bataiye|matlab)", re.IGNORECASE)

# What we recognise in the agent's own words (English and Hinglish, either script).
_SIGNAL_SHARED = re.compile(r"out[- ]of[- ]stock|stock-?outs?|स्टॉक|\bstock\b", re.IGNORECASE)
_CANCELLATIONS_RAISED = re.compile(r"cancel|कैंसल|कैन्सल|कैंसिल|रद्द", re.IGNORECASE)
_REC_GIVEN = [
    # 1: restock + something only the actual recommendation contains (a product, the timeframe, the reorder alert)
    re.compile(r"(?=.*(restock|रीस्टॉक|फिर से stock|दोबारा stock))"
               r"(?=.*(water bottle|tawa|storage jar|48|72|two to three days|2-3 (days|दिन)|reorder|रीऑर्डर|"
               r"दो[- ]तीन दिन|घंटे))", re.IGNORECASE | re.DOTALL),
    # 2: keep listings truthful: daily stock updates or pausing listings at zero
    re.compile(r"stock counts?|स्टॉक काउंट|pause.{0,25}listing|listing.{0,25}(pause|पॉज़|पाज़|पॉज)|लिस्टिंग.{0,25}(pause|पॉज़|पाज़|पॉज)|"
               r"small buffer|बफ़र|बफर", re.IGNORECASE | re.DOTALL),
    # 3: an instruction to run a promotion once stock is back (not an explanation of what promotions are)
    re.compile(r"(?=.*(run|launch|start|join|चला|शुरू).{0,45}(promotion|sponsored|sale event|प्रमोशन|स्पॉन्सर))"
               r"(?=.*(restock|रीस्टॉक|back in stock|stock is back|stock (वापस|आने|भरने)|स्टॉक (वापस|आने|भरने|फिर)))",
               re.IGNORECASE | re.DOTALL),
]
# The seller is asking what to do / to carry on: this is a request for the next step of the plan.
_WANTS_PLAN = re.compile(
    r"what (should|do|can|shall) (i|we) do|what('s| is)? next|next step|what else|how (do|can|should) (i|we) (fix|solve|improve|recover)|"
    r"go on|go ahead|carry on|continue|tell me the plan|the plan|what do you (suggest|recommend)|any (suggestion|advice)|"
    r"क्या करूँ|क्या करूं|क्या करना|क्या करें|आगे|और क्या|बताइए क्या|सुझाव|kya kar|aage|aur kya|sujhav", re.IGNORECASE)
_OPEN_ITEMS = [
    "share the key signal WITH its numbers (how much orders or sales fell, the price is unchanged so pricing is "
    "not the cause, out-of-stock products went from how many to how many) and ask ONE question about supply or "
    "restocking. Do not recommend anything yet.",
    "ask ONE question about the rise in cancellations (were they cancelled by the seller because stock ran out?).",
    "connect the dots in one sentence if you have not yet (stock-outs led to fewer orders, more cancellations and "
    "likely lower visibility), then give recommendation 1 ONLY: restock the out-of-stock best-sellers first (name "
    "the top products, the timeframe and the rough impact).",
    "give recommendation 2 ONLY: stop cancellations at the source (update stock counts daily, pause listings at "
    "zero stock).",
    "give recommendation 3 ONLY: after restocking, a short promotion or sponsored listing to rebuild visibility; "
    "then ask if the seller is comfortable with the plan.",
]


def _stage(msgs: list[dict[str, Any]]) -> tuple[bool, int, bool, list[bool]]:
    """(data loaded?, seller turns since the data was loaded, summary shown?, plan items done)

    Plan items: [key signal shared, cancellations raised, recommendation 1, 2, 3]. Each is detected
    from what the agent actually said, never from a turn count, so a detour (the seller asking about
    ads, chatting, pushing back) does not move the plan forward.
    """
    loaded, since, summary, done = False, 0, False, [False] * 5
    checks = [_SIGNAL_SHARED, _CANCELLATIONS_RAISED, *_REC_GIVEN]
    for m in msgs:
        for tc in m.get("tool_calls") or []:
            name = tc["function"]["name"]
            loaded = loaded or name == "get_seller_diagnosis"
            summary = summary or name == "show_call_summary"
        if loaded and m["role"] == "user":
            since += 1
        if loaded and m["role"] == "assistant" and m.get("content"):
            done = [d or bool(rx.search(m["content"])) for d, rx in zip(done, checks)]
    if any(done[2:]):
        done[0] = done[1] = True          # once advice has started, the diagnosis questions are behind us
    return loaded, since, summary, done


def _seller_replied_to_summary(msgs: list[dict[str, Any]]) -> bool:
    shown = max((i for i, m in enumerate(msgs)
                 if any(tc["function"]["name"] == "show_call_summary" for tc in m.get("tool_calls") or [])), default=None)
    return shown is not None and any(m["role"] == "user" for m in msgs[shown + 1:])


def stage_tools(msgs: list[dict[str, Any]], leaving: bool | None = None) -> list[dict[str, Any]]:
    loaded, since, summary, done = _stage(msgs)
    given = done[2:]
    if seller_is_leaving(msgs) if leaving is None else leaving:
        return [TOOLS["end_call"]]
    if not loaded:
        return [TOOLS["get_seller_diagnosis"]]
    if summary:
        # Hanging up is offered only once the seller has answered the recap ("anything else?"),
        # never in the same turn as the summary.
        return [TOOLS["end_call"]] if _seller_replied_to_summary(msgs) else []
    return [TOOLS["show_call_summary"]] if all(given) or since >= SUMMARY_AFTER_SELLER_TURNS else []


def stage_hint(msgs: list[dict[str, Any]], leaving: bool | None = None) -> str | None:
    """Notes for the model about where the call stands. Context, not a script: the seller leads."""
    loaded, _since, summary, done = _stage(msgs)
    if seller_is_leaving(msgs) if leaving is None else leaving:
        return ("The seller is ending the call. Say a warm goodbye in one short sentence (thank them by name, wish "
                "them well; if actions were agreed, mention you will check in in seven days) and call end_call. "
                "Do not ask another question.")
    if not loaded:
        return None
    if summary and _seller_replied_to_summary(msgs):
        nxt = ("if the seller has nothing more (for example they say no, that is all, or thank you), say a warm "
               "one-sentence goodbye and call end_call.")
    elif summary:
        nxt = ("give a recap of two sentences at most, offer a follow-up check-in in seven days and ask if there is "
               "anything else. Then stop and wait for the seller's answer.")
    elif not all(done):
        nxt = _OPEN_ITEMS[done.index(False)]
    else:
        nxt = ("all three recommendations are given. If the seller is comfortable with the plan, call the "
               "show_call_summary tool now (do not write it as text).")
    asked = _last_user_text(msgs)
    if _WANTS_DETAIL.search(asked) and not _WANTS_PLAN.search(asked) and not (summary and _seller_replied_to_summary(msgs)):
        return ("Call notes (context, not a script). FIRST respond to what the seller just said. They asked you "
                "something: answer exactly that, naturally and helpfully, in their style, and then stop. Do NOT add a "
                "recommendation, a next step or a new question about the account in this reply; you will come back "
                "to the plan when they are ready.")
    return ("Call notes (context, not a script). FIRST respond to what the seller just said. If they asked a "
            "question about anything, made small talk, pushed back or changed topic, answer THAT naturally and "
            "fully, in their style, and do not squeeze the plan into the same reply. Only when they are ready to "
            "move on (they answered your question, or said okay, go on, what next, what should I do), continue "
            "with the next open item of the plan: " + nxt)


# The seller can end the call at any point, not only after the summary. Detected in code from the
# seller's last message, so the model is offered `end_call` exactly when it makes sense.
_FAREWELL = re.compile(
    r"\b(bye|goodbye|good bye|see you|talk (to you )?later|that'?s all|that is all|that will be all|nothing else|"
    r"no(thing)? more questions|i'?m done|i am done|have a (good|nice|great) (day|evening|night)|"
    r"(end|disconnect|hang up|cut) (the |this )?call|i (have|need|got) to go)\b", re.IGNORECASE)
# Hindi / Hinglish farewells. No \b here: Devanagari vowel signs are not "word characters" for regex.
_FAREWELL_HI = re.compile(
    r"अलविदा|बस इतना ही|और कुछ नहीं|फिर मिलते|फिर बात करते|रखता हूँ|रखती हूँ|रखता हूं|रखती हूं|चलता हूँ|चलती हूँ|चलता हूं|चलती हूं|"
    r"बाय|नमस्ते[\s।.!]*$|alvida|bas itna hi|aur kuch nahi|phir milte|phir baat karte|rakhta h|rakhti h|chalta h|chalti h|"
    r"namaste[\s.!]*$", re.IGNORECASE)
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
DEFAULT_GOODBYE = "Thank you for your time, {name}. Have a great day. Goodbye!"
DEFAULT_GOODBYE_HI = "धन्यवाद {name} जी, आपका दिन शुभ हो। नमस्ते!"


def _last_user_text(msgs: list[dict[str, Any]]) -> str:
    last_user = next((m for m in reversed(msgs) if m["role"] == "user"), None)
    return (last_user or {}).get("content") or ""


def seller_is_leaving(msgs: list[dict[str, Any]]) -> bool:
    text = _last_user_text(msgs)
    return bool(_FAREWELL.search(text) or _FAREWELL_HI.search(text))


# --- understanding when the seller is wrapping up
# Literal goodbyes are caught by the patterns above at no cost. Many real endings are not literal
# ("ok thank you", "thanks Riya", "नहीं, धन्यवाद"), and the same words can also mean "go on"
# ("thanks, and what about pricing?"). For those, a small fast model reads the agent's last line and
# the seller's reply and decides. It only runs when the reply looks like a possible closing remark,
# so ordinary turns pay no extra latency.
_MAYBE_CLOSING = re.compile(
    r"\b(thanks?|thank ?(you|u)|thx|appreciate|no|nope|nothing|i'?m good|all good|all set|that helps|helpful)\b|"
    r"धन्यवाद|शुक्रिया|थैंक|नहीं|बस|shukriya|dhanyavaad|dhanyawad|nahi|bas\b", re.IGNORECASE)
_INTENT_MODELS = [("openai/gpt-oss-20b", {"reasoning_effort": "low"}), ("openai/gpt-oss-120b", {"reasoning_effort": "low"})]
_INTENT_PROMPT = """You decide whether a caller is ENDING a phone call with their account manager.
Answer YES if the caller's message is a closing remark: thanks or appreciation with no request, no
question and nothing they want next (examples: "ok thank you", "thanks Riya, that helps", "cool, will
do. thanks a lot, that's really helpful", "no, nothing else", "नहीं, धन्यवाद", "theek hai, thanks").
People usually end a call exactly like this, even if the manager had more to say.
Answer NO if they are agreeing or acknowledging and expect the manager to continue, answering the
manager's question, asking something, or raising a new point (examples: "thanks, and what about
prices?", "no, my stock is fine", "okay, what next?", "sure, I can do that").
Reply with exactly one word: YES or NO."""


def _ask_closing(agent_line: str, seller_line: str) -> bool:
    key = _groq_key()
    user = f'Manager said: "{agent_line[-400:]}"\nCaller replied: "{seller_line}"\nIs the caller ending the call?'
    for model, extra in _INTENT_MODELS:
        try:
            r = httpx.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, timeout=httpx.Timeout(6, connect=4),
                           json={"model": model, "temperature": 0, "max_completion_tokens": 80, **extra,
                                 "messages": [{"role": "system", "content": _INTENT_PROMPT}, {"role": "user", "content": user}]})
            if r.status_code == 200:
                return (r.json()["choices"][0]["message"].get("content") or "").strip().upper().startswith("YES")
        except httpx.HTTPError:
            continue
    return False


def closing_intent(msgs: list[dict[str, Any]], ask: Callable[[str, str], bool] = _ask_closing) -> bool:
    """True when the seller is wrapping up the call. If unsure or the check fails, the call continues:
    staying on the line by mistake is recoverable, hanging up by mistake is not."""
    if seller_is_leaving(msgs):
        return True
    text = _last_user_text(msgs).strip()
    if not text or len(text.split()) > 14 or "?" in text or not _MAYBE_CLOSING.search(text):
        return False
    agent_line = next((m.get("content") or "" for m in reversed(msgs) if m["role"] == "assistant" and m.get("content")), "")
    try:
        return bool(ask(agent_line, text))
    except Exception:  # noqa: BLE001 - any failure means "keep talking"
        return False


def seller_speaks_hindi(msgs: list[dict[str, Any]]) -> bool:
    """True when the seller's latest message is in Devanagari (speech-to-text writes Hindi that way)."""
    return bool(_DEVANAGARI.search(_last_user_text(msgs)))


def language_note(msgs: list[dict[str, Any]], hindi_voice: bool = True) -> str | None:
    """A per-turn reminder of the reply language. Recency matters: small models forget a rule
    that sits far up in the system prompt."""
    if not seller_speaks_hindi(msgs):
        spoke_hindi_before = any(m["role"] == "user" and _DEVANAGARI.search(m.get("content") or "") for m in msgs)
        return ("IMPORTANT, language for this reply: the seller's last message is in English, so reply ONLY in "
                "English, even though earlier turns were in Hindi.") if spoke_hindi_before else None
    if hindi_voice:
        return ("Language for this reply: the seller is speaking Hindi. Reply in simple everyday Hinglish written in "
                "Devanagari, keeping business words and product names in English, numbers in Hindi words, and "
                "feminine verb forms for yourself (देखती हूँ, समझ गई, करूँगी).")
    # The seller's device has no Hindi voice, so Devanagari could not be spoken aloud.
    return ("Language for this reply: the seller is speaking Hindi. Reply in natural Hinglish written in ROMAN "
            "letters only (no Devanagari), for example 'Aapke orders kam hue hain kyunki stock khatam ho gaya tha'.")


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
            body = {"model": model, "messages": messages, "temperature": FALLBACK_TEMPERATURE,
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


# reply cut-off points: a question mark, an opening brace/bracket, or a sentence end followed by a space
_STOP = re.compile(r"\?|[{\[]|[.!।](?=\s)")
_TOOL_NAMES = re.compile(r"\b(get_seller_diagnosis|show_on_screen|show_call_summary|end_call)\b(\(\))?")


_MONEY = re.compile(r"₹\s?(\d[\d,]*)(?:\.\d+)?")


def _speakable(text: str, hindi: bool = False) -> str:
    """Makes model text safe to read aloud: drops typographic characters voices read badly and tool
    names (a model that is not offered a tool sometimes writes its name as text), and turns money and
    percent symbols into words, because a voice reads "₹1,35,000" digit by digit or skips the symbol."""
    text = _TOOL_NAMES.sub("", text.translate(_SPEECH_FIXES))

    def money(m: re.Match) -> str:
        amount = int(m.group(1).replace(",", ""))
        return f"{m.group(1)} रुपये" if hindi else spoken_inr(amount)

    text = _MONEY.sub(money, text)
    return text.replace("%", " प्रतिशत" if hindi else " percent")


# ---------------------------------------------------------------- agent loop
def run_turn(history: list[dict[str, Any]], hindi_voice: bool = True, *,
             llm: Callable[..., Iterator[dict[str, Any]]] = groq_stream,
             leaving: Callable[[list[dict[str, Any]]], bool] = closing_intent) -> Iterator[dict[str, Any]]:
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
        wrapping_up = leaving(history)   # decided once, from the seller's message that started this turn
        speak_hindi = seller_speaks_hindi(history) and hindi_voice
        max_sentences = MAX_SENTENCES_EXPLAIN if _WANTS_DETAIL.search(_last_user_text(history)) else MAX_SENTENCES
        for _ in range(MAX_TOOL_ROUNDS):
            text, calls = "", []
            convo = history + new
            notes = [n for n in (language_note(convo, hindi_voice), stage_hint(convo, wrapping_up)) if n]
            prompt = messages + new + ([{"role": "system", "content": "\n".join(notes)}] if notes else [])
            asked = False  # a question ends the agent's turn; anything after it is dropped
            sentences = 0
            held = ""      # last, possibly unfinished word: held back so a word split across chunks is filtered whole

            def release(chunk: str) -> Iterator[dict[str, Any]]:
                nonlocal text, spoke, asked
                nonlocal sentences
                chunk = _speakable(chunk, speak_hindi)
                if asked or not chunk:
                    return
                # Guard rails. A reply stops at: (1) its first question, since some models answer themselves
                # and keep talking; (2) a brace, since speech never contains one and it means the model is
                # writing tool data as text; (3) MAX_SENTENCES sentences, so one reply holds one idea.
                for m in _STOP.finditer(chunk):
                    if m.group() in "{[":
                        chunk, asked = chunk[:m.start()].rstrip(" ("), True
                    elif m.group().startswith("?"):
                        chunk, asked = chunk[:m.start() + 1], True   # a question hands the turn to the seller
                    else:
                        sentences += 1
                        if sentences >= max_sentences:
                            chunk, asked = chunk[:m.start() + 1], True
                    if asked:
                        break
                if not chunk:
                    return
                if spoke and not text:
                    yield {"type": "text", "delta": " "}  # keep rounds from running together
                text += chunk
                spoke = True
                yield {"type": "text", "delta": chunk}

            for ev in llm(prompt, stage_tools(convo, wrapping_up)):
                if "text" in ev:
                    held += ev["text"]
                    cut = max(held.rfind(" "), held.rfind("\n")) + 1
                    ready, held = held[:cut], held[cut:]
                    yield from release(ready)
                elif "tool_calls" in ev:
                    calls = ev["tool_calls"]
            yield from release(held)
            if not text.strip():
                text = ""
            if not text and not calls and not spoke:
                # Guard rail: the model returned nothing. Ask once more, plainly; a call must never go silent.
                retry = prompt + [{"role": "system", "content": "Reply to the seller now, in one or two natural spoken sentences."}]
                held = ""
                for ev in llm(retry, []):
                    if "text" in ev:
                        held += ev["text"]
                yield from release(held)
                if not text.strip():
                    text = "जी, एक बार फिर बताइए?" if speak_hindi else "Sorry, I missed that. Could you say it once more?"
                    spoke = True
                    yield {"type": "text", "delta": text}
            if any(c["name"] == "end_call" for c in calls) and not spoke:
                # Guard rail: never hang up silently. If the model ended the call without a goodbye, add one.
                hindi = seller_speaks_hindi(history) and hindi_voice
                text = (DEFAULT_GOODBYE_HI if hindi else DEFAULT_GOODBYE).format(name=cfg.SELLER_NAME)
                spoke = True
                yield {"type": "text", "delta": text}
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
