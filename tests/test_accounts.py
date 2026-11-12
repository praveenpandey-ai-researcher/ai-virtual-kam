"""Tests for the ElevenLabs account chain. No network: the probe is faked."""
import asyncio

from app.accounts import Account, elevenlabs_accounts, pick_account


def chain(*states):
    """states: for each account "ok", "exhausted" or "" (no agent set up)."""
    accounts = [Account(i, f"key{i}", "" if s == "" else f"agent{i}") for i, s in enumerate(states)]
    probed = []

    async def probe(acc):
        probed.append(acc.index)
        if not acc.agent_id:
            return None, "agent not set up"
        return (f"token{acc.index}", "ok") if states[acc.index] == "ok" else (None, "credits exhausted (12 left)")

    return accounts, probe, probed


def pick(states, after=-1):
    accounts, probe, probed = chain(*states)
    account, token, skipped = asyncio.run(pick_account(accounts, after, probe))
    return (account.index if account else None), token, skipped, probed


def test_first_account_with_credits_is_used():
    assert pick(["ok", "ok", "ok"])[:2] == (0, "token0")


def test_exhausted_accounts_are_skipped_in_order():
    index, token, skipped, probed = pick(["exhausted", "exhausted", "ok", "ok"])
    assert (index, token) == (2, "token2")
    assert probed == [0, 1, 2]                      # stops at the first that works; account 4 is not touched
    assert skipped == ["account 1: credits exhausted (12 left)", "account 2: credits exhausted (12 left)"]


def test_accounts_without_an_agent_are_skipped():
    assert pick(["", "ok"])[:2] == (1, "token1")


def test_all_exhausted_means_fallback():
    index, token, skipped, _ = pick(["exhausted", "", "exhausted"])
    assert index is None and token is None and len(skipped) == 3


def test_chain_continues_after_an_account_that_dropped_the_call():
    # account 1 issued a token but dropped the call in the browser; the browser asks again with after=0
    assert pick(["ok", "ok", "ok"], after=0)[:2] == (1, "token1")
    assert pick(["ok", "ok"], after=1)[0] is None    # nothing left: fallback


def test_accounts_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEYS", "k1, k2 ,k3")
    monkeypatch.setenv("ELEVENLABS_AGENT_IDS", "a1,,a3")
    assert elevenlabs_accounts() == [Account(0, "k1", "a1"), Account(1, "k2", ""), Account(2, "k3", "a3")]
    # single-account variables still work when the list is not set
    monkeypatch.delenv("ELEVENLABS_API_KEYS"); monkeypatch.delenv("ELEVENLABS_AGENT_IDS")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "solo"); monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent")
    assert elevenlabs_accounts() == [Account(0, "solo", "agent")]


def test_full_chain_all_five_exhausted_results_in_fallback():
    # 5 accounts, all exhausted
    states = ["exhausted", "exhausted", "exhausted", "exhausted", "exhausted"]
    index, token, skipped, probed = pick(states)
    assert index is None
    assert token is None
    assert probed == [0, 1, 2, 3, 4]
    assert len(skipped) == 5
    assert all("credits exhausted" in s for s in skipped)


def test_chain_picks_later_account_when_earlier_ones_exhausted():
    # Accounts 1, 2, 3 exhausted, account 4 ok
    states = ["exhausted", "exhausted", "exhausted", "ok", "exhausted"]
    index, token, skipped, probed = pick(states)
    assert index == 3
    assert token == "token3"
    assert probed == [0, 1, 2, 3]
    assert len(skipped) == 3
