# Mini AI Voice KAM

A browser-based **voice** assistant that acts as a Virtual Key Account Manager for a marketplace seller.
The seller calls in saying *"My sales have dropped and I don't know why"*. The agent greets them, pulls up
their account data, asks focused questions to confirm the cause, recommends 2–3 practical actions and ends
with a summary and a follow-up commitment. As the conversation moves along, the screen shows only the data
being discussed.

Built as a take-home assignment for an AI First Intern role. All data is mock data.

## How it works

```
Browser (mic + speaker, live transcript, data panel)
   │  WebRTC audio                         ▲ client tools
   ▼                                       │
ElevenLabs Agent  ── speech-to-text → LLM (Gemini 2.5 Flash) → text-to-speech
   │  tool calls: get_seller_diagnosis · show_on_screen · show_call_summary
   ▼
FastAPI backend ── /api/diagnosis → Python diagnosis engine (deterministic, unit-tested)
               └─ /api/session   → short-lived conversation token (API key stays on the server)
```

**Key design decision:** every number the agent says comes from the Python diagnosis engine, never from
the LLM. The engine compares this month with last month, ranks likely causes, rules out what isn't the
cause (pricing) and estimates the ₹ impact. The LLM explains and discusses those results in conversation.

## Two voice modes, one brain

| | Primary: ElevenLabs agent | Automatic fallback: free stack |
|---|---|---|
| Hearing and speaking | ElevenLabs real-time voice (WebRTC) | The browser's built-in speech recognition and speech synthesis |
| LLM | Gemini 2.5 Flash (inside the agent) | Open models on Groq's free tier, rotated on rate limits |
| Agent loop | Managed by ElevenLabs | Our own loop in `app/free_agent.py`, streamed to the browser |
| When it is used | By default | When ElevenLabs has no credits, cannot issue a token, or hangs up on connect; or with `?mode=free` |

Both modes share the system prompt, the tools, the diagnosis engine and the on-screen experience, so the
advice is identical. Because smaller open models drift from long prompts, the fallback adds deterministic
guard rails in code: tools are offered only at the right stage of the call (the model cannot end the call
before the summary), a one-line next-step hint keeps it on plan, and a reply is cut at its first question.

For the best fallback voice use Microsoft Edge (natural Indian-English voice); headphones allow interrupting by voice.

## What the agent concludes from the sample data

| Metric | Last month | This month |
|---|---|---|
| Sales | ₹2,00,000 | ₹1,35,000 |
| Orders | 400 | 270 |
| Cancellation rate | 8% | 14% |
| Avg selling price | ₹500 | ₹500 |
| Out-of-stock products | 5 | 18 |

- The price didn't change, so the whole drop is fewer orders. **Pricing is ruled out.**
- **Root cause: stock-outs.** One inventory problem explains all three symptoms: fewer live products,
  more seller cancellations, and likely lower visibility.
- **Actions:** restock best-sellers first (≈ ₹56,500/month), stop cancellations at the source by updating
  stock daily (≈ ₹8,100/month), then rebuild visibility with a promotion.

Assumptions (catalogue size, product list) are documented in [`docs/01_PRD.pdf`](docs/01_PRD.pdf).

## Run locally

Requires Python 3.10+ and an [ElevenLabs](https://elevenlabs.io) API key with Agents access.

```bash
pip install -r requirements.txt
cp .env.example .env              # add ELEVENLABS_API_KEY and/or GROQ_API_KEY
python scripts/setup_agent.py     # creates the agent + tools, saves ELEVENLABS_AGENT_ID to .env
uvicorn app.main:app --port 8000
```

Then open http://localhost:8000 in Chrome and click **Start call**.
To preview the visuals without a call, open http://localhost:8000/?demo=1.

## Project layout

| Path | Purpose |
|---|---|
| `app/diagnosis.py` | Diagnosis engine: metrics, rules, ranked findings, recommendations |
| `app/agent_config.py` | Agent persona, system prompt, tools, voice and LLM (configuration as code) |
| `app/main.py` | FastAPI: page, diagnosis endpoint, voice-mode selection, fallback turn endpoint |
| `app/free_agent.py` | Fallback agent loop on Groq: model rotation, stage guard rails, streaming |
| `app/static/` | Voice call UI: live transcript, conversation-driven data panel |
| `app/data/seller_data.json` | Sample seller data from the brief, plus labelled mock assumptions |
| `scripts/setup_agent.py` | Creates or updates the ElevenLabs agent and tools through the API |
| `scripts/text_test.py` | Runs scripted conversations against the live ElevenLabs agent in text-only mode |
| `scripts/fallback_test.py` | Runs a scripted conversation through the fallback agent |
| `tests/` | Unit tests for the diagnosis engine and the fallback agent loop (`python -m pytest -q`) |
| `docs/` | PRD and design notes |
