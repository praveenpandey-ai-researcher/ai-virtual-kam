"""Tests for the fallback agent loop. A scripted fake LLM is injected, so no network is needed."""
import json

from app import free_agent
from app.diagnosis import agent_view, diagnose, load_seller_data


def scripted_llm(*rounds):
    """Each round is (text, [(tool_name, args), ...]). Records the messages it was called with."""
    calls, offered = [], []
    it = iter(rounds)

    def llm(messages, tools):
        calls.append(messages)
        offered.append([f["function"]["name"] for f in tools])
        text, tools = next(it)
        if text:
            yield {"text": text}
        yield {"tool_calls": [{"id": f"c{i}", "name": n, "arguments": json.dumps(a)} for i, (n, a) in enumerate(tools)]}

    llm.calls, llm.offered = calls, offered
    return llm


def test_plain_reply():
    llm = scripted_llm(("Hello there.", []))
    events = list(free_agent.run_turn([{"role": "user", "content": "hi"}], llm=llm))
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "Hello there."
    assert events[-1] == {"type": "done", "messages": [{"role": "assistant", "content": "Hello there."}]}
    assert llm.calls[0][0]["role"] == "system"  # system prompt is added server-side


def test_tool_round_feeds_diagnosis_back_to_llm():
    llm = scripted_llm(("Let me pull up your account.", [("get_seller_diagnosis", {"seller_concern": "sales dropped"})]),
                       ("Your orders fell.", []))
    events = list(free_agent.run_turn([{"role": "user", "content": "sales dropped"}], llm=llm))
    tools = [(e["name"], e["args"]) for e in events if e["type"] == "tool"]
    assert tools == [("get_seller_diagnosis", {"seller_concern": "sales dropped"})]
    # the second LLM call must contain the tool result with real engine numbers
    tool_msg = next(m for m in llm.calls[1] if m["role"] == "tool")
    assert "₹2,00,000 -> ₹1,35,000" in tool_msg["content"]
    done = events[-1]
    assert done["type"] == "done" and [m["role"] for m in done["messages"]] == ["assistant", "tool", "assistant"]
    # guard rails: data tool offered first, then no tools; a next-step hint is added once data is loaded
    assert llm.offered == [["get_seller_diagnosis"], []]
    assert llm.calls[1][-1]["role"] == "system" and "Do not recommend anything yet" in llm.calls[1][-1]["content"]


def test_end_call_stops_the_loop():
    llm = scripted_llm(("Goodbye!", [("end_call", {})]))
    events = list(free_agent.run_turn([{"role": "user", "content": "bye"}], llm=llm))
    assert {"type": "tool", "name": "end_call", "args": {}} in events
    assert len(llm.calls) == 1


def test_llm_unavailable_is_reported():
    def llm(_messages, _tools):
        raise free_agent.LLMUnavailable("All models are busy")
        yield  # pragma: no cover

    events = list(free_agent.run_turn([{"role": "user", "content": "hi"}], llm=llm))
    assert events == [{"type": "error", "message": "All models are busy"}]


def test_bad_tool_arguments_do_not_crash():
    def llm(_messages, _tools):
        yield {"tool_calls": [{"id": "x", "name": "show_on_screen", "arguments": "{not json"}]}

    events = list(free_agent.run_turn([{"role": "user", "content": "hi"}], llm=llm))
    assert {"type": "tool", "name": "show_on_screen", "args": {}} in events


def test_agent_view_is_compact_and_complete():
    full = diagnose(load_seller_data())
    view = agent_view(full)
    text = json.dumps(view, ensure_ascii=False)
    assert len(text) < 0.75 * len(json.dumps(full, ensure_ascii=False))
    for fact in ["₹2,00,000 -> ₹1,35,000", "400 -> 270", "8% -> 14%", "5 -> 18", "no change", "Stainless Steel Water Bottle 1L"]:
        assert fact in text
    assert len(view["recommendations"]) == 3


def _call(name):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": "x", "type": "function", "function": {"name": name, "arguments": "{}"}}]}


def _offered(msgs):
    return [t["function"]["name"] for t in free_agent.stage_tools(msgs)]


def _said(text):
    return {"role": "assistant", "content": text}


USER = {"role": "user", "content": "ok"}
LOADED = [USER, _call("get_seller_diagnosis")]
SIGNAL = _said("Your orders fell and eighteen products are out of stock. Any supply problem?")
CANCELS = _said("Were most of the cancellations yours?")
RECS = [_said("Restock your best-sellers first, like the water bottle, within two to three days."),
        _said("Update stock counts daily and pause listings at zero."),
        _said("Once you have restocked, run a short promotion on those best-sellers.")]


def test_tools_are_gated_by_stage():
    assert _offered([USER]) == ["get_seller_diagnosis"]
    assert _offered(LOADED) == []                                   # cannot summarise or hang up early
    assert _offered(LOADED + [SIGNAL, CANCELS] + RECS[:2]) == []
    done = LOADED + [SIGNAL, CANCELS] + RECS
    assert _offered(done) == ["show_call_summary"]                  # all three recommendations were spoken
    shown = done + [_call("show_call_summary")]
    assert _offered(shown) == []                                    # not in the same turn as the summary
    assert _offered(shown + [{"role": "user", "content": "no"}]) == ["end_call"]
    # safety net: after many turns the summary is offered even if a recommendation was not detected
    assert _offered(LOADED + [USER] * free_agent.SUMMARY_AFTER_SELLER_TURNS) == ["show_call_summary"]


def test_stage_hints_follow_what_was_said_not_the_turn_count():
    hint = free_agent.stage_hint
    assert hint([USER]) is None
    assert "supply or restocking" in hint(LOADED)
    # a detour (the seller asks about ads, chats, pushes back) does not advance the plan
    assert "supply or restocking" in hint(LOADED + [USER, _said("Sponsored listings are paid placements."), USER, USER])
    assert "cancellations" in hint(LOADED + [SIGNAL, USER])
    assert "recommendation 1 ONLY" in hint(LOADED + [SIGNAL, USER, CANCELS, USER])
    assert "recommendation 2 ONLY" in hint(LOADED + [USER, _said("पहले अपने बेस्ट-सेलर्स को 48-72 घंटे में रीस्टॉक करें।"), USER])
    assert "show_call_summary" in hint(LOADED + [SIGNAL, CANCELS] + RECS)
    assert "wait for the seller" in hint(LOADED + [_call("show_call_summary")])
    assert "call end_call" in hint(LOADED + [_call("show_call_summary"), USER])
    # every hint puts the seller first
    assert "FIRST respond to what the seller just said" in hint(LOADED)


def test_reply_is_cut_at_the_first_question():
    def llm(_messages, _tools):
        yield {"text": "Update stock daily. Shall we continue?"}
        yield {"text": " Yes, that works for me. Finally, run a promotion."}
        yield {"tool_calls": []}

    events = list(free_agent.run_turn([{"role": "user", "content": "ok"}], llm=llm))
    assert events[-1]["messages"] == [{"role": "assistant", "content": "Update stock daily. Shall we continue?"}]


def test_typographic_characters_are_made_speakable():
    assert free_agent._speakable("best‑sellers — restock *now*") == "best-sellers ,  restock now"
    assert free_agent._speakable("Sales fell 32.5% to ₹1,35,000.") == "Sales fell 32.5 percent to 1 lakh 35 thousand rupees."
    assert free_agent._speakable("sales ₹56,500 वापस", hindi=True) == "sales 56,500 रुपये वापस"


def test_seller_can_end_the_call_at_any_point():
    early = [{"role": "user", "content": "Actually I have to go, bye."}]
    assert _offered(early) == ["end_call"]
    assert "goodbye" in free_agent.stage_hint(early)
    for phrase in ["Okay thanks, that's all.", "No, nothing else.", "Please end the call", "Have a nice day!"]:
        assert free_agent.seller_is_leaving([{"role": "user", "content": phrase}]), phrase
    for phrase in ["My sales dropped", "Yes, my supplier was late", "Should I buy more stock?"]:
        assert not free_agent.seller_is_leaving([{"role": "user", "content": phrase}]), phrase


def test_never_hangs_up_without_a_goodbye():
    llm = scripted_llm(("", [("end_call", {})]))   # the model ends the call but says nothing
    events = list(free_agent.run_turn([{"role": "user", "content": "bye"}], llm=llm))
    spoken = "".join(e["delta"] for e in events if e["type"] == "text")
    assert "Goodbye" in spoken and "Praveen" in spoken
    assert events.index(next(e for e in events if e["type"] == "text")) < events.index({"type": "tool", "name": "end_call", "args": {}})


HINDI = {"role": "user", "content": "मेरी sales इस महीने बहुत कम हो गई हैं"}


def test_language_note_follows_the_seller():
    assert free_agent.language_note([{"role": "user", "content": "My sales dropped"}]) is None
    assert "Devanagari" in free_agent.language_note([HINDI])
    assert "ROMAN" in free_agent.language_note([HINDI], hindi_voice=False)   # device has no Hindi voice
    # switching back to English is called out explicitly
    back = free_agent.language_note([HINDI, {"role": "assistant", "content": "जी"}, {"role": "user", "content": "okay thanks"}])
    assert "ONLY in English" in back


def test_language_note_reaches_the_llm():
    llm = scripted_llm(("जी, मैं देखती हूँ।", []))
    list(free_agent.run_turn([HINDI], llm=llm))
    assert "Hinglish" in llm.calls[0][-1]["content"]


def test_hindi_farewells_end_the_call():
    for phrase in ["ठीक है धन्यवाद, बस इतना ही", "अच्छा चलता हूँ", "theek hai, phir milte hain", "ओके बाय", "धन्यवाद, नमस्ते"]:
        assert free_agent.seller_is_leaving([{"role": "user", "content": phrase}]), phrase
    for phrase in ["नमस्ते, मेरी sales कम हो गई हैं", "हाँ, supplier ने देर कर दी", "स्टॉक खत्म हो गया था"]:
        assert not free_agent.seller_is_leaving([{"role": "user", "content": phrase}]), phrase


def test_default_goodbye_is_in_the_sellers_language():
    def silent_end(_messages, _tools):
        yield {"tool_calls": [{"id": "x", "name": "end_call", "arguments": "{}"}]}

    spoken = "".join(e["delta"] for e in free_agent.run_turn([{"role": "user", "content": "ओके बाय"}], llm=silent_end) if e["type"] == "text")
    assert "नमस्ते" in spoken


def test_tool_names_are_never_spoken_even_when_split_across_chunks():
    def llm(_messages, _tools):
        for piece in ["Update stock daily.", " show_", "call_", "summary", " Thanks."]:
            yield {"text": piece}
        yield {"tool_calls": []}

    events = list(free_agent.run_turn([{"role": "user", "content": "ok"}], llm=llm))
    spoken = "".join(e["delta"] for e in events if e["type"] == "text")
    assert "show" not in spoken and "summary" not in spoken
    assert spoken.split() == ["Update", "stock", "daily.", "Thanks."]


def test_reply_stops_at_data_and_is_capped():
    def spilled(_messages, _tools):
        yield {"text": 'Update stock daily. ({"key_issue": "stock-outs"}) We found the cause.'}
        yield {"tool_calls": []}

    out = "".join(e["delta"] for e in free_agent.run_turn([{"role": "user", "content": "ok"}], llm=spilled) if e["type"] == "text")
    assert out.strip() == "Update stock daily."

    def rambling(_messages, _tools):
        yield {"text": "One. Two is 32.5 percent. Three. Four. Five. Six. Seven. Eight. Nine."}
        yield {"tool_calls": []}

    def say(seller):
        return "".join(e["delta"] for e in free_agent.run_turn([{"role": "user", "content": seller}], llm=rambling)
                       if e["type"] == "text").strip()

    assert say("ok") == "One. Two is 32.5 percent. Three. Four."
    # the seller asked for an explanation: a longer answer is allowed
    assert say("Can you explain how sponsored listings work") == "One. Two is 32.5 percent. Three. Four. Five. Six. Seven."



def _closing(agent, seller, verdict):
    asked = []

    def ask(a, s):
        asked.append((a, s))
        return verdict

    msgs = [{"role": "assistant", "content": agent}, {"role": "user", "content": seller}]
    return free_agent.closing_intent(msgs, ask), asked


def test_closing_intent_uses_the_model_only_for_ambiguous_closers():
    # literal goodbye: decided by pattern, model not asked
    assert _closing("Anything else?", "No, that's all, bye", False) == (True, [])
    # ordinary turns: model not asked, call continues
    for line in ["Yes, my supplier delayed two shipments.", "Okay, what should I do?", "Sure, I can do that.", "हाँ, ठीक है"]:
        assert _closing("Shall we go through the plan?", line, True) == (False, []), line
    # a question is never a goodbye, even with "thanks" in it
    assert _closing("Restock first.", "Thanks, and what about my prices?", True) == (False, [])
    # ambiguous closers: the model decides
    ended, asked = _closing("Anything else I can help with?", "No, thank you Riya", True)
    assert ended and asked == [("Anything else I can help with?", "No, thank you Riya")]
    assert _closing("Did you face a supply problem?", "No, my stock is fine", False)[0] is False
    assert _closing("और कुछ?", "नहीं, धन्यवाद", True)[0] is True


def test_closing_intent_fails_safe():
    def broken(_a, _s):
        raise RuntimeError("model down")

    msgs = [{"role": "assistant", "content": "Anything else?"}, {"role": "user", "content": "ok thank you"}]
    assert free_agent.closing_intent(msgs, broken) is False     # never hang up because of an error


def test_wrapping_up_offers_end_call_and_goodbye_hint():
    llm = scripted_llm(("You're welcome, Praveen. Goodbye!", [("end_call", {})]))
    msgs = [{"role": "assistant", "content": "Anything else?"}, {"role": "user", "content": "ok thank you"}]
    events = list(free_agent.run_turn(msgs, llm=llm, leaving=lambda _m: True))
    assert llm.offered == [["end_call"]]
    assert "goodbye" in llm.calls[0][-1]["content"]
    assert {"type": "tool", "name": "end_call", "args": {}} in events


def test_general_answers_do_not_count_as_recommendations():
    ads = _said("Sponsored listings are paid placements. They are worth it especially when you have just restocked "
                "your best-sellers, because the extra visibility helps recover sales faster.")
    assert _offered(LOADED + [SIGNAL, CANCELS, ads]) == []
    assert "recommendation 1 ONLY" in free_agent.stage_hint(LOADED + [SIGNAL, CANCELS, ads, USER])


def test_a_question_gets_an_answer_without_a_plan_item_attached():
    ask = lambda text: free_agent.stage_hint(LOADED + [SIGNAL, {"role": "user", "content": text}])
    for q in ["How do sponsored listings actually work?", "can you explain why stock-outs hurt ranking", "sponsored listing कैसे काम करती है?"]:
        assert "then stop" in ask(q) and "recommendation" not in ask(q).replace("Do NOT add a recommendation", ""), q
    # asking what to do IS a request for the plan
    for q in ["ok so what should I do first?", "what next?", "ठीक है, तो मुझे क्या करना चाहिए?"]:
        assert "next open item" in ask(q), q


def test_an_empty_model_reply_is_retried_and_never_silent():
    replies = iter(["", "Got it, that explains a lot."])

    def llm(_messages, _tools):
        text = next(replies)
        if text:
            yield {"text": text}
        yield {"tool_calls": []}

    out = "".join(e["delta"] for e in free_agent.run_turn([{"role": "user", "content": "my supplier was late"}], llm=llm) if e["type"] == "text")
    assert out.strip() == "Got it, that explains a lot."

    def always_empty(_messages, _tools):
        yield {"tool_calls": []}

    out = "".join(e["delta"] for e in free_agent.run_turn([{"role": "user", "content": "hmm"}], llm=always_empty) if e["type"] == "text")
    assert "say it once more" in out
