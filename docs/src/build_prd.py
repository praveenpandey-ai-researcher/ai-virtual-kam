"""Generates docs/01_PRD.pdf  —  run: python docs/src/build_prd.py"""
from pathlib import Path

from pdf_style import P, h1, h2, bullets, table, callout, build, Spacer, mm, KeepTogether

OUT = Path(__file__).resolve().parents[1] / "01_PRD.pdf"
W = 174 * mm  # usable width

story = [
    P("Product Requirements Document", "title"),
    P("Mini AI Voice KAM: a voice-first Virtual Key Account Manager for Snapdeal sellers", "subtitle"),
    table([
        ["Version", "Status", "Owner", "Deadline"],
        ["v1.0", "Draft for review", "Candidate (AI First Intern)", "Within 24 h of receiving the brief"],
    ], [W * 0.15, W * 0.25, W * 0.30, W * 0.30]),

    # ------------------------------------------------------------------ 1
    h1("1. Summary"),
    P("A seller calls a Virtual KAM because sales have dropped. We will build a <b>browser-based voice "
      "agent</b> that greets the seller, listens, asks focused diagnostic questions, reads the seller's "
      "metrics, identifies the root cause, recommends <b>2–3 practical actions</b> and ends with a spoken "
      "and on-screen <b>summary of issues, actions and next steps</b>."),
    P("The guiding idea comes from the hiring email: the agent should not just answer questions. It "
      "should <b>take ownership</b> of the seller's problem. It comes to the call already having read "
      "the data, forms a hypothesis, checks it with the seller and drives the next action."),

    # ------------------------------------------------------------------ 2
    h1("2. Problem statement"),
    P("Sellers usually see that sales fell but not <i>why</i>. Human KAMs cannot cover the long tail "
      "of small sellers, so most of them get no timely, data-backed guidance. A Virtual KAM that "
      "turns raw metrics into a clear cause and a short action plan, over a natural voice call, "
      "closes this gap at scale."),
    callout("“My sales have dropped significantly this month. I don’t understand why. "
            "Can you help me?”", "Scenario given in the brief"),

    # ------------------------------------------------------------------ 3
    h1("3. Target user"),
    table([
        ["Attribute", "Assumed profile"],
        ["Who", "Small or mid-size Snapdeal seller who runs their own inventory and listings"],
        ["Context", "Busy, may be calling from a phone or browser, wants a fast answer and not a report"],
        ["Data literacy", "Knows their products well; less comfortable reading dashboards and metrics"],
        ["Needs", "A plain-language reason for the drop, actions they can take this week, and someone "
                  "who follows up"],
    ], [W * 0.22, W * 0.78]),

    # ------------------------------------------------------------------ 4
    h1("4. Goals and non-goals"),
    h2("Goals"),
    bullets([
        "Hold a natural, two-way voice conversation that the seller can interrupt (rubric: 30 marks).",
        "Understand the seller's concern and ask relevant follow-up questions (20 marks).",
        "Diagnose the decline from the sample data and give 2–3 specific, usable recommendations (20 marks).",
        "Use a clean, explainable AI workflow where numbers come from code and not from the LLM (20 marks).",
        "Add a few touches that show product thinking: a live insights panel and a call summary (10 marks).",
    ]),
    h2("Non-goals"),
    bullets([
        "Real Snapdeal data, systems, authentication or integrations (the brief rules these out).",
        "Telephony / phone numbers, mobile apps, multi-language support (English only for v1).",
        "Actually executing changes on the seller's account. Actions are recorded as an action plan.",
        "A polished UI. The interface stays minimal and functional.",
    ]),

    # ------------------------------------------------------------------ 5
    h1("5. Data analysis"),
    P("The brief supplies one month-over-month snapshot. We analyse it first, because the quality of "
      "the bot's advice depends entirely on reading this table correctly."),
    table([
        ["Metric", "Last month", "This month", "Change", "What it tells us"],
        ["Sales", "₹2,00,000", "₹1,35,000", "<b>−32.5%</b>", "Revenue fell by ₹65,000"],
        ["Orders", "400", "270", "<b>−32.5%</b>", "Same % as sales, so the whole drop is volume"],
        ["Average selling price", "₹500", "₹500", "0%", "Pricing did <b>not</b> change; price is not the cause"],
        ["Cancellation rate", "8%", "14%", "<b>+6 pts (1.75×)</b>", "32 → ~38 cancelled orders, even though "
                                                                    "orders fell"],
        ["Out-of-stock products", "5", "18", "<b>+13 (3.6×)</b>", "Big inventory gap; the strongest "
                                                                  "signal in the data"],
    ], [W * 0.22, W * 0.14, W * 0.14, W * 0.15, W * 0.35]),
    Spacer(1, 6),
    h2("5.1 Root-cause hypothesis: one inventory problem, three symptoms"),
    P("All three bad metrics can be explained by a single cause: <b>stock-outs</b>."),
    bullets([
        "<b>Fewer live listings → fewer orders.</b> Out-of-stock products cannot be bought and are "
        "typically pushed down or hidden in search. With 13 more products unavailable, the seller "
        "simply had less to sell.",
        "<b>Stock running out after orders are placed → more cancellations.</b> When inventory is "
        "not kept in sync, customers order items the seller no longer has, and the seller cancels.",
        "<b>Cancellations and stock-outs → lower visibility.</b> Marketplaces generally rank "
        "reliable sellers higher. A rising cancellation rate can reduce ranking even for in-stock "
        "products, so the drop can feed itself.",
    ]),
    callout("Under assumption A3 (a catalogue of ~50 products), live products fell from 45 to 32 "
            "(−29%), close to the −32.5% drop in orders. Orders <i>per live product</i> stayed "
            "roughly flat (8.9 → 8.4). In other words, demand for each listed product held up; the "
            "seller lost sales mainly because fewer products were available to buy.",
            "Key insight the bot should explain to the seller"),
    h2("5.2 Size of the opportunity (estimates, under stated assumptions)"),
    table([
        ["Lever", "Estimate", "Basis"],
        ["Restock the 13 extra out-of-stock products", "≈ 110–115 orders ≈ <b>₹55k–58k / month</b>",
         "13 products × ~8.4–8.9 orders per live product (section 5.1)"],
        ["Bring cancellations back from 14% to 8%", "≈ 16 orders ≈ <b>₹8k / month</b>",
         "6% of 270 orders × ₹500"],
        ["Delivered value, for context", "₹1,84,000 → ₹1,16,100 (<b>−37%</b>)",
         "Orders × (1 − cancellation rate) × ₹500: the realised drop is worse than headline sales"],
    ], [W * 0.36, W * 0.30, W * 0.34]),

    # ------------------------------------------------------------------ 6
    h1("6. Assumptions (stated openly, as the brief requires)"),
    table([
        ["ID", "Assumption", "Why it is reasonable / how it is used"],
        ["A1", "“Sales” is gross order value (orders × ASP), before cancellations.",
         "400 × ₹500 = ₹2,00,000 and 270 × ₹500 = ₹1,35,000 match exactly."],
        ["A2", "Order counts include orders that were later cancelled.",
         "Follows from A1; used to estimate delivered value."],
        ["A3", "The seller lists ~50 active products in one category (Home &amp; Kitchen).",
         "Not given in the brief; needed to reason about the share of the catalogue that is unavailable. "
         "The bot calls this an estimate."],
        ["A4", "Some of the 18 out-of-stock products were recent best-sellers.",
         "Typical pattern: fast movers run out first. A small mock product list illustrates this."],
        ["A5", "Most of the extra cancellations are seller-initiated, caused by stock running out.",
         "Treated as a hypothesis. The bot <b>asks the seller to confirm</b>, it does not assert it."],
        ["A6", "Snapdeal-style marketplace mechanics: out-of-stock listings lose visibility and high "
               "cancellation rates hurt seller ranking.",
         "General marketplace behaviour; described as typical, not as Snapdeal policy."],
    ], [W * 0.07, W * 0.43, W * 0.50]),
    P("Extended mock data (illustrative and clearly labelled in the app): seller name, category and a "
      "short list of top out-of-stock products with last month's units sold. This lets the bot make "
      "concrete suggestions such as “restock these three first”.", "small"),

    # ------------------------------------------------------------------ 7
    h1("7. Functional requirements"),
    P("FR1–FR6 map one-to-one to section 3 of the brief. FR7–FR8 are our additions."),
    table([
        ["ID", "Requirement", "Acceptance criteria"],
        ["FR1", "<b>Greet and understand intent.</b> Warm greeting as the seller's Virtual KAM; "
                "asks how it can help.",
         "Greeting within ~2 s of starting the call; correctly identifies “sales dropped” intent "
         "from free speech."],
        ["FR2", "<b>Natural two-way voice.</b> Speech in, speech out, with turn-taking and "
                "interruption (barge-in).",
         "Seller can interrupt the bot mid-sentence; replies are short (1–3 sentences); no typing needed."],
        ["FR3", "<b>Relevant follow-up questions.</b> Hypothesis-driven, one question at a time.",
         "Asks at least 2 questions that test the hypothesis (e.g. which products ran out and why; "
         "who is cancelling orders)."],
        ["FR4", "<b>Use seller data to find issues</b> (pricing, inventory, cancellations, visibility).",
         "Bot fetches metrics via a tool; every number it speaks matches the data; explicitly rules "
         "out pricing."],
        ["FR5", "<b>Recommend 2–3 practical actions.</b>",
         "Each action is specific, has a reason tied to the data and a timeframe."],
        ["FR6", "<b>Summarise at the end:</b> key issues, recommendations, next steps.",
         "Spoken summary of ≤ 30 s plus the same summary shown on screen."],
        ["FR7", "<b>Live insights panel</b> (addition).",
         "Page shows the metrics table and the findings once the bot has analysed the data."],
        ["FR8", "<b>Ownership and follow-up</b> (addition).",
         "Bot proposes a concrete follow-up (e.g. check-in after 7 days with the metrics to watch)."],
    ], [W * 0.07, W * 0.46, W * 0.47]),

    # ------------------------------------------------------------------ 8
    h1("8. Conversation design"),
    table([
        ["Stage", "Bot behaviour", "Example line (illustrative)"],
        ["1. Greet", "Introduce itself as the seller's Virtual KAM; open question.",
         "“Hi, I’m your Snapdeal account manager. How can I help today?”"],
        ["2. Acknowledge", "Show empathy, confirm the problem, say it has already looked at the account.",
         "“I can see that. Your orders fell from 400 to 270 this month. Let’s find out why.”"],
        ["3. Diagnose", "Share the main signal, then ask one question at a time to confirm it.",
         "“Your price is unchanged, but 18 products are out of stock compared with 5 last month. "
         "Did you have a supply problem?”"],
        ["4. Probe", "Ask about cancellations and their cause; adapt to the seller's answers.",
         "“Cancellations went up to 14%. Are those mostly orders you couldn’t fulfil?”"],
        ["5. Explain", "Connect the dots in plain language (one cause, three symptoms).",
         "“So stock-outs are hurting you twice: fewer products to sell, and cancellations that "
         "lower your ranking.”"],
        ["6. Recommend", "2–3 prioritised actions with the expected impact.",
         "See section 9."],
        ["7. Summarise and commit", "Recap issue, actions, next step; propose a follow-up; close politely.",
         "“To recap… I’ll check in next week to see if orders are recovering.”"],
    ], [W * 0.17, W * 0.38, W * 0.45]),
    h2("Conversation rules"),
    bullets([
        "Speak like a person on a phone call: short sentences, no lists or symbols read aloud, numbers said naturally.",
        "One question per turn; always wait for the seller's answer.",
        "Never invent numbers. Only use values returned by the data tool, and say so when something is an estimate.",
        "If the seller's answer contradicts the hypothesis (e.g. stock is fine), revise the diagnosis instead of insisting.",
        "Politely steer off-topic requests back to the sales problem; say “I don’t have that data” when true.",
    ]),

    # ------------------------------------------------------------------ 9
    h1("9. Recommendation playbook"),
    P("The bot picks and orders recommendations based on the diagnosis and the seller's answers. "
      "For the sample data the expected set is:"),
    table([
        ["#", "Action", "Why (tied to data)", "When / impact"],
        ["R1", "<b>Restock the out-of-stock products, best-sellers first</b>, and set a reorder "
               "alert (e.g. at ~2 weeks of stock cover).",
         "18 products out of stock vs 5; orders per live product held steady, so restocking "
         "should bring orders back.",
         "Within 48–72 h; largest lever, ≈ ₹55k–58k / month."],
        ["R2", "<b>Stop cancellations at the source:</b> update stock counts daily, deactivate or "
               "pause listings at zero stock, keep a small buffer.",
         "Cancellations up from 8% to 14%, likely from selling stock that was not there.",
         "This week; ≈ ₹8k / month and protects ranking."],
        ["R3", "<b>Rebuild visibility after restocking:</b> short promotion or sponsored listing "
               "on restocked best-sellers; join an upcoming sale event if eligible.",
         "Stock-outs and cancellations likely pushed listings down; ranking recovers faster "
         "with a sales push.",
         "After restock; speeds up recovery."],
    ], [W * 0.06, W * 0.36, W * 0.33, W * 0.25]),
    P("Explicitly <b>not</b> recommended: cutting prices. The average selling price is unchanged, "
      "so a discount would reduce margin without fixing the cause. Saying this shows the bot reasons "
      "rather than giving generic advice.", "body"),

    # ------------------------------------------------------------------ 10
    h1("10. Non-functional requirements"),
    table([
        ["Area", "Requirement"],
        ["Latency", "Bot starts speaking within ~1.5 s of the seller finishing a turn (perceived)."],
        ["Naturalness", "Natural English voice; barge-in supported; filler-free, concise answers."],
        ["Accuracy", "Zero invented figures: all metrics come from the data tool; estimates labelled as such."],
        ["Security", "ElevenLabs API key stays on the server (.env); the browser gets a short-lived session token only."],
        ["Reliability", "If the data tool fails, the bot says so and continues with general guidance instead of crashing."],
        ["Simplicity", "One Python service plus one HTML page; runs locally with one command; deployable for a demo link."],
    ], [W * 0.18, W * 0.82]),

    # ------------------------------------------------------------------ 11
    h1("11. Solution overview"),
    P("Full detail goes in the technical note. At a high level:"),
    table([
        ["Component", "Choice", "Role"],
        ["Voice agent", "ElevenLabs Agents (Conversational AI)",
         "Speech-to-text, turn-taking, interruption handling, LLM orchestration, text-to-speech"],
        ["LLM", "Selected inside the ElevenLabs agent (finalised during build)",
         "Conversation, reasoning, choosing when to call tools"],
        ["Backend", "Python + FastAPI",
         "Serves the page, issues secure session tokens, hosts the seller-data and diagnosis tool"],
        ["Diagnosis engine", "Plain Python rules",
         "Computes changes, ranks likely causes and estimates impact. Deterministic and testable"],
        ["Frontend", "Single HTML/JS page",
         "Start/stop call, live transcript, metrics and findings panel, end-of-call summary"],
    ], [W * 0.18, W * 0.34, W * 0.48]),
    callout("The LLM is good at conversation and judgement but unreliable at arithmetic. So the "
            "Python diagnosis engine computes every number and ranks causes, and the LLM only explains "
            "and discusses the results. This makes the bot's advice accurate, testable and easy to explain.",
            "Key design decision: numbers come from code, conversation comes from the LLM"),

    # ------------------------------------------------------------------ 12
    h1("12. How the solution maps to the evaluation rubric"),
    table([
        ["Criterion (marks)", "How we address it"],
        ["Voice interaction &amp; natural conversation (30)",
         "Managed real-time voice with barge-in; prompt tuned for short, spoken-style turns; tested for latency."],
        ["Understanding seller queries (20)",
         "Intent recognition from free speech; hypothesis-driven follow-ups; adapts when the seller disagrees."],
        ["Quality of business recommendations (20)",
         "Root cause linking all three symptoms; 2–3 prioritised actions with reasons, timeframes and ₹ impact; "
         "explains why a price cut is not advised."],
        ["AI implementation &amp; workflow (20)",
         "Clear separation: tool-based data access, deterministic diagnosis, LLM for dialogue; secure key handling."],
        ["Creativity &amp; execution (10)",
         "Live insights panel, on-screen call summary, proactive follow-up commitment."],
    ], [W * 0.34, W * 0.66]),

    # ------------------------------------------------------------------ 13
    h1("13. Test plan (acceptance scenarios)"),
    table([
        ["#", "Scenario", "Expected behaviour"],
        ["T1", "Happy path: seller states the brief's line and answers questions cooperatively.",
         "Identifies stock-outs as the root cause, gives R1–R3, ends with a summary."],
        ["T2", "Seller interrupts the bot mid-sentence.", "Bot stops, listens, responds to the interruption."],
        ["T3", "Seller says “my stock is fine”.", "Bot does not insist; probes cancellations and visibility instead."],
        ["T4", "Seller asks “should I reduce my prices?”", "Explains that price is unchanged and a cut would not fix the cause."],
        ["T5", "Off-topic question (e.g. payment settlement).", "Acknowledges, says it has no data on that, steers back."],
        ["T6", "Number check across the whole call.", "Every figure spoken matches the data or is labelled an estimate."],
    ], [W * 0.06, W * 0.44, W * 0.50]),

    # ------------------------------------------------------------------ 14
    h1("14. Risks and mitigations"),
    table([
        ["Risk", "Mitigation"],
        ["ElevenLabs free-tier minutes run out during testing", "Test logic in text first; keep voice tests short; record the demo early."],
        ["Speech recognition mishears numbers or “₹”", "Bot reads numbers from the tool, not from the transcript; confirms key facts."],
        ["LLM states made-up figures", "Numbers only via tool; prompt forbids invented data; test T6."],
        ["Robotic or long-winded replies", "Prompt rules for short spoken turns; iterate on real test calls."],
        ["Microphone blocked in browser", "Serve over HTTPS when deployed; clear on-screen permission prompt."],
        ["Time pressure (24 h)", "Build the minimum end-to-end first; extras (panel, summary card) only after it works."],
    ], [W * 0.40, W * 0.60]),

    # ------------------------------------------------------------------ 15
    h1("15. Deliverables"),
    table([
        ["Deliverable", "Required by brief?", "Format"],
        ["Working demo link or runnable prototype", "Yes", "Deployed URL + GitHub repo with README"],
        ["1–2 minute screen recording of a real voice call", "Yes", "MP4"],
        ["Short note (≤ 1 page): models, voice platform, workflow", "Yes", "PDF"],
        ["This PRD", "No (supporting)", "PDF"],
        ["Interview prep book: every decision and the reasoning behind it", "No (for the candidate)", "PDF"],
        ["Demo script", "No (supporting)", "PDF"],
    ], [W * 0.52, W * 0.22, W * 0.26]),

    h1("16. Open questions"),
    bullets([
        "Which LLM inside the ElevenLabs agent gives the best latency/quality balance? Decide by quick A/B test during build.",
        "Hosting choice for the demo link (e.g. Render vs Railway). Decide at deployment.",
        "Which voice best fits an Indian seller audience while staying in English? Pick after listening to 2–3 options.",
    ]),
]

if __name__ == "__main__":
    build(str(OUT), story, "PRD v1.0")
    print(f"Wrote {OUT}")
