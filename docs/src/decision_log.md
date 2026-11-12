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

## D17. Automatic fallback voice mode (free stack) when ElevenLabs is unavailable
- **What happened:** Testing used up the ElevenLabs free credits (10,000). ElevenLabs then connects and immediately hangs up ("agent not available"), so the demo was dead until the monthly reset.
- **Decision:** Keep ElevenLabs as the primary voice and add an automatic fallback with zero running cost: browser speech recognition + browser speech synthesis for voice, and our own agent loop on Groq's free tier for the LLM (`app/free_agent.py`).
- **How the app chooses:** `/api/session` checks the ElevenLabs credit balance and tries to get a token; if either fails it returns `mode: "free"`. If ElevenLabs connects and drops before saying anything, the page switches to the fallback by itself. `?mode=free` forces it for testing.
- **Why this was easy:** The "brain" (diagnosis engine, prompt, tools, UI) was already separate from the voice layer, so only the voice layer needed a second implementation. Both modes sit behind one small interface in the browser (`call = { end(), meter() }`).
- **Model choice (measured, not guessed):** Benchmarked the Groq models available to the account on our real prompt and tools. `openai/gpt-oss-120b` (reasoning effort low) was fastest to the tool call (~1.0 s) and used the numbers correctly; `gpt-oss-20b` and `qwen3.8-27b` are next in the rotation. Reasoning-heavy settings were avoided because thinking time is silence on a voice call.
- **Rate limits:** Free tier = 8,000 tokens/minute per model. Three mitigations: (1) a compact `agent_view` of the diagnosis for the LLM (about half the size, same facts); (2) the `show_on_screen` tool is dropped in this mode, because each tool call costs a full LLM round; the keyword mechanism reveals the cards instead; (3) on a 429 the server instantly retries on the next model, so the three allowances add up. The browser also says "Give me a moment" and retries once more.
- **Guard rails in code (key learning):** The smaller model drifted from the long prompt: it recommended before diagnosing, showed the summary early, answered its own question and ended the call by itself. Stricter wording was not reliable, so the stage of the conversation is computed deterministically from the history and controls (a) which tools the model is offered (no `end_call` before the summary exists), (b) a one-line next-step hint that still lets it answer the seller first, and (c) a cut-off at the first question in a reply. All unit-tested with a fake LLM.
- **Perceived latency:** The first turn needs two LLM rounds (fetch data, then answer). The page says "Let me pull up your account" the instant the fetch starts, and replies are spoken sentence by sentence as they stream.
- **Trade-offs (be honest about these):** Browser voices are less natural than ElevenLabs; without echo cancellation the mic is muted while Riya speaks unless the seller is on headphones (then voice interruption works), otherwise tap/Space interrupts; speech recognition needs Chrome or Edge.
- **How to explain:** "I hit a real production problem (quota exhaustion) and turned it into resilience: the app degrades gracefully to a free stack instead of going down, and the premium voice returns automatically when credits do."

## D18. Fallback listening: own voice-activity detection + Whisper, not the browser's SpeechRecognition
- **What happened:** The first fallback used the browser's built-in SpeechRecognition. On the user's machine it did not detect speech at all, and it fails silently, so there was nothing to debug.
- **Decision:** Record the microphone ourselves. A small voice-activity detector (RMS loudness against an adaptive noise floor, 50 ms ticks) decides when the seller starts and stops talking (1.1 s pause ends the turn); the clip is sent to `/api/free/transcribe`, which uses Whisper (`whisper-large-v3-turbo`, with `whisper-large-v3` as a second rate-limit bucket) on Groq, with a domain prompt so words like "restocking" and "cancellations" are spelled correctly.
- **Why:** Works in every modern browser, is more accurate on Indian accents, and every step is ours to observe and test. The screen now shows the state: Listening → Hearing you… → Got it… → Riya is thinking… → Riya is speaking.
- **Also fixed:** (1) A watchdog on speech output: Chrome sometimes never reports that an utterance ended, which would leave the app stuck on "speaking" and never listening. (2) The audio context is explicitly resumed; one created after an `await` can start suspended and read pure silence. (3) Very short noises that Whisper turns into "Thank you." are ignored.
- **Tested with real audio:** Chrome's fake-microphone flag plays a synthesized spoken sentence into the page; the test checks that it is detected, transcribed and answered.
- **Trade-off:** About 1 s extra for transcription compared with streaming recognition, and no live "words appearing as you speak".

## D19. Fallback mode: automatic interruption (no checkbox) and self-ending calls
- **Requirement:** Voice activity detection should be fully automatic (no "I'm on headphones" checkbox), and the agent should end the call by itself, with a goodbye, when the conversation is over.
- **Automatic interruption (barge-in):** The detector runs all the time. While Riya speaks, the first 700 ms calibrate how loud her own voice arrives at the microphone (`echoPeak`); after that only sound clearly louder than that (1.7x + margin, sustained ~300 ms with a slow decay across gaps between words) counts as the seller. Recording starts at the first loud moment so the opening words are kept. Second line of defence: if the captured clip is at least 75% Riya's own words, it is discarded as echo.
- **Bug found by tracing (good interview story):** With the browser's `echoCancellation: true`, the microphone level dropped to ~0.005 whenever the page was speaking and jumped back to ~0.36 the moment it stopped: the browser was muting the seller during playback, so interruption could never trigger. Fix: echo cancellation and auto gain are off in this mode; our own threshold handles echo. A second bug: the echo estimate adapted instantly, so it "learned" the start of the seller's speech as echo; now it may only creep up 1% per tick. A third: cancelling speech fires "end" events that restarted the recording; now ignored unless Riya is actually speaking.
- **Self-ending calls:** A farewell detector (regex on the seller's last message: "bye", "that's all", "nothing else", "I have to go", ...) offers the `end_call` tool at any stage and tells the model to say a one-sentence goodbye. If the model ends the call without saying anything, code inserts a default goodbye. The browser hangs up only after the goodbye has finished playing. The ElevenLabs prompt got the same rule.
- **Silence handling:** After 20 s of silence Riya asks "Are you still there?"; after another 20 s she says goodbye and ends the call.
- **How it was tested:** Chrome's fake microphone plays a synthesized seller sentence (a) after the greeting: detected, transcribed, answered; (b) during the greeting: Riya stops within ~0.5 s and the full sentence is transcribed; (c) typed farewells early and mid-call: goodbye spoken, call ended, no button pressed.

## D20. Language auto-match: Hindi / Hinglish (fallback mode)
- **Why:** Many marketplace sellers are more comfortable in Hindi. The agent replies in the language the seller last used, with no toggle: English gets English; Hindi or Hinglish gets everyday Hinglish (Hindi sentences, business words such as orders, stock, cancellation in English), which is how sellers and account managers really talk.
- **Hearing:** Whisper auto-detects the language on clips with at least 1.2 s of voiced sound (a first threshold of 2.5 s was too strict: a live test showed a normal Hindi sentence falling under it and being forced into English); shorter clips ("yes", "हाँ") use the conversation's current language because detection is unreliable on them. Whisper often labels Hindi as Urdu (same speech, different script), so any result that is not English or Hindi is transcribed again as Hindi.
- **Thinking:** A language section in the prompt plus a per-turn language note next to the stage hint (recency matters for small models). Found in testing and fixed: Riya used masculine verb forms ("समझ गया"); she now uses feminine ones ("समझ गई"). She was too formal ("रद्दीकरण"); the prompt now asks for everyday words ("cancellation") with an example sentence.
- **Speaking:** Each sentence is spoken with a Hindi voice if it contains Devanagari, otherwise the English voice. If the device has no Hindi voice, the server is told and the model writes Hinglish in Roman letters so the English voice can read it.
- **Everything else follows the language:** the "let me pull up your account" filler, the "are you still there?" check, the default goodbye, farewell detection ("बस इतना ही", "चलता हूँ", "phir milte hain"), and the keywords that reveal the on-screen cards. Cards and the summary stay in English, like a seller dashboard.
- **Guard rails tightened while testing Hindi:** (1) stage hints now follow which recommendations were actually spoken (detected in both languages), not the turn count, because the model sometimes got a step ahead; (2) a reply is cut at three sentences, at its first question, or at a brace (the model was writing tool data as text); tool names are never spoken; (3) the hang-up tool is offered only after the seller has answered the recap or said goodbye.
- **Tested:** scripted English and Hindi conversations against the live model; real Hindi and English audio through `/api/free/transcribe`; a Hindi sentence played into the browser's fake microphone (detected, transcribed in Devanagari, Hindi voice selected, Hinglish reply).
- **Not done yet:** Hindi in the ElevenLabs mode. It needs their multilingual speech model and language settings, which cannot be tested until the account has credits.

## D21. Ending the call by understanding, not keywords
- **Problem:** The call ended only on literal phrases ("bye", "that's all"). Real endings are often not literal ("ok thank you Riya", "नहीं, धन्यवाद"), and the same words can also mean "go on" ("thanks, and what about prices?", "no, my stock is fine").
- **Decision:** Two tiers. Literal goodbyes are caught by patterns at no cost. If the seller's message merely *looks like* a possible closing remark (contains thanks / no / nothing-type words, is short, has no question mark), a small fast model reads Riya's last line and the seller's reply and answers YES or NO. On YES the main model is offered `end_call` and told to say a one-sentence goodbye; the browser hangs up after the goodbye has been spoken.
- **Fail-safe:** Any error or doubt means "keep talking". Staying on the line by mistake is recoverable; hanging up by mistake is not.
- **Cost:** About one second, and only on turns that look like a closing remark. Ordinary turns ("yes, my supplier was late", "okay, what next?") never trigger the check.
- **Tested live:** "No, thank you Riya", "नहीं, धन्यवाद", "ok thanks… I think I'm good" end the call; "No, my stock is fine", "thanks, yes please go ahead" do not.

## D22. UI redesign and conversation starters
- **Design:** Soft white cards on a light background, rosy accent, a large call orb with a single "Call" button, and a welcome panel (Riya mascot, greeting, three starter chips) that the data cards replace once the conversation begins. The call panel shrinks during a call so the transcript has room.
- **Starter chips** were built and then removed at the user's request: a voice call should start with the seller simply talking, not with preset buttons. The mascot stays.
- **Honesty in the UI:** The reference design said 'You can also say "Call Riya"'. A wake word would need the microphone open before the call, which we do not do, so the line says "Speak naturally, in English or Hindi" instead. The mascot is an original drawing. The Snapdeal mark was redrawn as a vector from the brand image supplied for the assignment (measured outline, 95% pixel overlap with the reference).
- **Speech clean-up:** Money and percent symbols in model text ("₹1,35,000", "32.5%") are turned into words in code before being spoken.

## D23. Seller-led conversation: answer anything, talk the way the seller talks
- **Feedback:** The agent felt scripted. It should use the LLM's strength: answer whatever the seller asks and match how they speak.
- **What was wrong:** My guard rails pushed a fixed plan. Hints said "do step N now", counted turns to decide the step, capped every reply at three sentences, and the prompt said "stay on the seller's business, redirect unrelated topics".
- **Prompt rewrite (both voice modes):** A persona with experience and warmth; "talk like a person, and like THIS person" (mirror language, formality, pace, length, mood; natural reactions; no call-centre phrases); "you can talk about anything the seller brings up" (selling online in general, ads, pricing, returns, small talk). The diagnosis plan is described as a default path, not a script.
- **Two honest limits kept:** (1) numbers about this account come only from the data tool; if a figure is not there, say so and still help; (2) for exact Snapdeal policies, fees and dates give general guidance and point to the Seller Panel, never invent specifics. Requests that would hurt the seller (fake reviews) are declined kindly with an honest alternative.
- **Guard rails changed from directing to supporting:**
  - Plan progress is detected from what the agent actually said (signal shared, cancellations raised, each recommendation), never from a turn count, so a detour does not advance the plan.
  - The per-turn note says "FIRST respond to what the seller just said". If the seller asked a question that is not "what should I do", the note contains no plan item at all: answer and stop. The plan resumes when they say okay / what next.
  - Reply length follows the seller: four sentences normally, seven when they asked for an explanation.
  - Detection was made strict after a test showed a general answer about sponsored listings being counted as "recommendation 3 given", which triggered the summary early.
  - A silent turn (the model returned nothing) is retried once and can never reach the seller as silence.
  - Kept: no invented account numbers, tools gated by stage, a question ends the turn, hang-up only on a real ending.
- **Tested live:** a casual seller ("hey riya, how's your day going?", "my sales totally tanked"), an off-plan question about sponsored listings, "explain in detail why stock-outs hurt ranking", "what if I buy fake reviews?", then "what should I do first?". She chats, answers each on its own, declines the fake reviews, and returns to the plan only when asked. The standard cooperative flow still runs diagnosis, three recommendations, summary, goodbye.
- **Trade-off:** More freedom means less predictability. Wording varies between runs (temperature 0.6), and the fixed order of the demo is no longer guaranteed if the seller wanders.
