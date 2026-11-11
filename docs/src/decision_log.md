# Decision log (source for the interview prep book PDF)

Each entry: decision, why, alternatives considered, and how to explain it.

## D1. Numbers come from code, conversation comes from the LLM
- **Decision:** A deterministic Python diagnosis engine (`app/diagnosis.py`) computes all metrics, ranks causes and estimates impact. The LLM only explains and discusses the results it receives through a tool call.
- **Why:** LLMs are good at dialogue and judgement but unreliable at arithmetic and prone to inventing figures. Wrong numbers in business advice destroy trust.
- **Alternatives:** Paste the data into the prompt and let the LLM reason over it. Simpler, but there's no guarantee on accuracy and it can't be tested.
- **How to explain:** "The bot can't hallucinate a number because it never calculates one. Every figure it says is produced and unit-tested in Python."

## D2. Rules are generic thresholds, not hardcoded to the sample
- **Decision:** Rules fire on month-over-month changes (ASP change < 2% → pricing ruled out; +3 stock-outs → inventory issue; +2 pts cancellations → cancellation issue).
- **Why:** Shows the engine would work for any seller, not just this dataset. Tests prove it (price-change and healthy-month cases).
- **How to explain:** "The engine is a small rules system. Swap in another seller's data and it gives a different diagnosis."

## D3. Root cause = one inventory problem with three symptoms
- **Evidence:** Orders −32.5% with flat ASP; stock-outs 5 → 18; live products 45 → 32 (−29%); orders per live product flat (8.9 → 8.4).
- **Why it matters:** Connecting the symptoms is what a real KAM does. Listing three separate problems is what a generic chatbot does.

## D4. Mock product list designed to be consistent with the brief
- **Decision:** Added 13 newly out-of-stock products whose last-month orders sum to 113 (≈ ₹56,500), consistent with the 13 × ~8.9 average estimate.
- **Why:** Lets the bot name specific products to restock first, without contradicting the brief's figures. Clearly labelled illustrative.

## D5. Pricing is explicitly ruled out
- **Decision:** The engine returns a `ruled_out` list and tells the agent not to suggest price cuts.
- **Why:** Saying what is *not* the cause shows reasoning and stops the most common generic advice ("offer discounts"), which would hurt margin here.

## D6. Speech-friendly outputs
- **Decision:** The engine returns Indian-format amounts (₹1,35,000) for screen and spoken forms ("1 lakh 35 thousand rupees") for voice.
- **Why:** Voice is 30% of the score. Text-to-speech reading "₹1,35,000" or "135000" sounds unnatural to an Indian seller.

## D7. Cancellation cause is a hypothesis the bot confirms
- **Decision:** Cancellation finding has confidence "medium" and comes with questions for the seller; visibility is "low" confidence (inferred, no data).
- **Why:** Honest about uncertainty; the bot asks instead of asserting, which also makes the conversation two-way.

## D8. ElevenLabs Agents platform for the voice layer
- **Decision:** Use ElevenLabs Agents (Conversational AI) for speech-to-text, turn-taking, interruption handling, LLM orchestration and text-to-speech, over WebRTC.
- **Why:** Voice and natural conversation is 30% of the marks. Building STT → LLM → TTS by hand means solving latency, turn detection and barge-in ourselves; a managed real-time pipeline does this well out of the box. Our engineering effort goes into the business logic instead.
- **Alternatives:** Hand-built pipeline (Whisper/Deepgram + LLM + TTS): more control, much more latency work. Browser Web Speech API: free but robotic and unreliable. Vapi/Retell: similar to ElevenLabs; chose ElevenLabs for voice quality.
- **How to explain:** "I bought the commodity (real-time voice) and built the differentiator (diagnosis + conversation design)."

## D9. LLM = Gemini 2.5 Flash (inside the ElevenLabs agent), temperature 0.3
- **Why:** Voice needs low time-to-first-token; Flash-class models are fast and reliable at tool calling. Heavy reasoning isn't needed because the analysis is done in Python (D1). Temperature 0.3: consistent but not robotic.
- **Swappable:** One line in `app/agent_config.py` + `python scripts/setup_agent.py`.

## D10. Voice = "Jessica" (warm, conversational), TTS model = eleven_flash_v2
- **Why:** Flash is ElevenLabs' lowest-latency English model. Warm conversational voice fits an account manager persona. An Indian-English voice would fit sellers better (future improvement).

## D11. Agent configured as code
- **Decision:** Prompt, tools, voice and LLM live in `app/agent_config.py`; `scripts/setup_agent.py` creates/updates the live agent and tools idempotently through the API.
- **Why:** Version-controlled, reviewable, reproducible. Anyone can recreate the agent from the repo. No hidden dashboard settings.

## D12. Client tools in the browser + private agent with short-lived tokens
- **Decision:** `get_seller_diagnosis` and `show_call_summary` are *client* tools: the agent asks the browser, which calls our FastAPI backend and updates the UI at the same moment. The agent is private (`enable_auth`); the browser gets a short-lived WebRTC token from `/api/session`, so the API key never leaves the server.
- **Why:** Client tools work locally without a public webhook URL, and make the UI react exactly when the agent "looks at the data" (the analysis panel lights up). Security: no secrets in the browser.
- **Alternative:** Server (webhook) tools: need a public HTTPS URL even in development.

## D13. Prompt design principles
- One question per turn; replies under ~40 words; no lists or symbols (it's speech).
- Call the data tool immediately, in the same turn ("Let me pull up your account"), never invent numbers.
- Hypothesis-driven: share the key signal, then ask a question to confirm the cause.
- **Facts vs hypotheses:** metrics are facts, causes are hypotheses. If the seller disputes a fact ("my stock is fine"), the agent politely shows the evidence and offers an explanation (listing stock counts not updated). If they dispute a hypothesis, it adapts.
- Never recommend price cuts (ASP unchanged); summary tool uses digits/₹ (screen), speech uses words.

## D14. Text-only test harness before voice testing
- **Decision:** `scripts/text_test.py` runs scripted seller conversations against the *same live agent* in text-only mode over WebSocket, answering client tools with the real engine.
- **Why:** Free-tier credits are limited (10,000). Text mode skips speech synthesis and lets us iterate on the prompt quickly and repeatably. Note: it is cheaper than voice but not free; six text conversations used about 2,900 credits in total (usage appears with a delay), so tests are run sparingly.
- **Scenarios:** happy path, seller pushback ("my stock is fine"), price-cut request.
- **Bugs it caught (and fixes):** (1) agent said "let me check" then waited for the seller → prompt now requires calling the tool in the same turn; (2) recap too long → max three sentences; (3) third recommendation skipped → "recommend ALL actions, one per turn"; (4) summary card used spoken numbers → tool description asks for digits; (5) agent gave up on a fact when the seller disagreed → facts-vs-hypotheses rule.

## D15. Live "thinking" UI: analysing animation, typewriter reveal, data visuals
- **Decision:** When the agent calls `get_seller_diagnosis`, the panel shows an "analysing" checklist (sales, prices, inventory, cancellations, impact), then reveals the result section by section with a typewriter effect: headline → "where last month's sales went" bar → cause chain → findings → ruled-out → action cards. The call summary is typed the same way. A live waveform shows Riya's voice / the seller's mic.
- **Why:** Makes the agent's reasoning visible, like a human KAM sharing their screen. It shows *what the AI looked at and why it concluded this*, which builds trust and makes a better demo video.
- **Key detail (latency):** The animation is purely cosmetic and runs in parallel. The tool returns the data to the agent immediately, so the voice response is not delayed by the animation.
- **Honest numbers:** The bar chart's split (₹1,35,000 kept / ₹56,500 stock-outs / ₹8,500 other) comes from `revenue_bridge` in the Python engine and is unit-tested to add up to ₹2,00,000. The browser computes nothing.
- **Chart choices:** One stacked bar (parts of a whole) instead of a pie; hatched fill for the inferred/uncertain part; status colours always paired with an icon and label; hover tooltips; respects the OS "reduce motion" setting.
- **Preview mode:** `/?demo=1` plays the whole sequence without a call (no credits); `&instant=1` skips animation.

## D16. The screen follows the conversation (nothing shown before the call)
- **Decision:** The data panel starts empty. Sections appear one at a time, only when the conversation reaches that topic: sales overview → pricing → inventory → cancellations → how it connects → each action as it is recommended → call summary.
- **How:** A third client tool, `show_on_screen(topics)`, lets the agent push the section it is talking about (fire-and-forget: `expects_response: false`, so it adds no wait). As a safety net, the page also scans the agent's spoken text for topic keywords, so the screen keeps up even if the LLM skips a tool call. Reveals are idempotent and queued, so sections never repeat or animate on top of each other.
- **Why:** Feels like a real account manager sharing their screen and pointing at one thing at a time, instead of dumping a dashboard. It keeps the seller's attention on what is being said (voice-first), and it proves the UI is driven by the agent, not pre-rendered.
- **Why two mechanisms:** The tool is intent-based (the agent decides); the keyword net is deterministic. In text tests the agent pushed inventory, cancellations, root cause and each action itself; "pricing" was mentioned without a tool call and was caught by the keyword net.
- **Trade-off considered:** Keyword-only is simpler but brittle to phrasing; tool-only depends on the LLM never forgetting. Together they are robust.
