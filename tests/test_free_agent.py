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
    events = list(free_agent.run_turn([{"role": "user", "content": "hi"}], llm))
    assert events[0] == {"type": "text", "delta": "Hello there."}
    assert events[-1] == {"type": "done", "messages": [{"role": "assistant", "content": "Hello there."}]}
    assert llm.calls[0][0]["role"] == "system"  # system prompt is added server-side


def test_tool_round_feeds_diagnosis_back_to_llm():
    llm = scripted_llm(("Let me pull up your account.", [("get_seller_diagnosis", {"seller_concern": "sales dropped"})]),
                       ("Your orders fell.", []))
    events = list(free_agent.run_turn([{"role": "user", "content": "sales dropped"}], llm))
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
    events = list(free_agent.run_turn([{"role": "user", "content": "bye"}], llm))
    assert {"type": "tool", "name": "end_call", "args": {}} in events
    assert len(llm.calls) == 1


def test_llm_unavailable_is_reported():
    def llm(_messages, _tools):
        raise free_agent.LLMUnavailable("All models are busy")
        yield  # pragma: no cover

    events = list(free_agent.run_turn([{"role": "user", "content": "hi"}], llm))
    assert events == [{"type": "error", "message": "All models are busy"}]


def test_bad_tool_arguments_do_not_crash():
    def llm(_messages, _tools):
        yield {"tool_calls": [{"id": "x", "name": "show_on_screen", "arguments": "{not json"}]}

    events = list(free_agent.run_turn([{"role": "user", "content": "hi"}], llm))
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


def test_tools_are_gated_by_stage():
    user = {"role": "user", "content": "ok"}
    assert _offered([user]) == ["get_seller_diagnosis"]
    loaded = [user, _call("get_seller_diagnosis")]
    assert _offered(loaded) == []  # cannot summarise or hang up early
    late = loaded + [user] * free_agent.SUMMARY_AFTER_SELLER_TURNS
    assert _offered(late) == ["show_call_summary"]
    assert _offered(late + [_call("show_call_summary")]) == ["end_call"]


def test_stage_hints_follow_the_plan():
    user = {"role": "user", "content": "ok"}
    loaded = [user, _call("get_seller_diagnosis")]
    assert free_agent.stage_hint([user]) is None
    assert "supply or restocking" in free_agent.stage_hint(loaded)
    assert "cancellations" in free_agent.stage_hint(loaded + [user])
    assert "recommendation 1 ONLY" in free_agent.stage_hint(loaded + [user] * 3)
    assert "call end_call" in free_agent.stage_hint(loaded + [_call("show_call_summary")])


def test_reply_is_cut_at_the_first_question():
    def llm(_messages, _tools):
        yield {"text": "Update stock daily. Shall we continue?"}
        yield {"text": " Yes, that works for me. Finally, run a promotion."}
        yield {"tool_calls": []}

    events = list(free_agent.run_turn([{"role": "user", "content": "ok"}], llm))
    assert events[-1]["messages"] == [{"role": "assistant", "content": "Update stock daily. Shall we continue?"}]


def test_typographic_characters_are_made_speakable():
    assert free_agent._speakable("best‑sellers — restock *now*") == "best-sellers ,  restock now"


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
    events = list(free_agent.run_turn([{"role": "user", "content": "bye"}], llm))
    spoken = "".join(e["delta"] for e in events if e["type"] == "text")
    assert "Goodbye" in spoken and "Praveen" in spoken
    assert events.index(next(e for e in events if e["type"] == "text")) < events.index({"type": "tool", "name": "end_call", "args": {}})
