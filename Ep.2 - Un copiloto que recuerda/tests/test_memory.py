"""Tests for the Episode 2 conversational memory layer.

These tests are deterministic and do NOT depend on Bedrock: the ``summarize``
strategy is exercised with an injected fake summarizer, and persistence /
truncation use the local JSON backend only. This matches the requirement that
persistence and truncation tests never call the model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_copilot import state
from code_copilot.memory import (
    JsonFileStore,
    MemoryStore,
    SessionManager,
    buildStore,
    countTokens,
    prune,
    sanitizeSessionId,
)


def _msg(role: str, text: str) -> dict:
    """Build a minimal Strands-style message."""
    return {"role": role, "content": [{"text": text}]}


# --- Persistence ------------------------------------------------------------

def test_jsonFileStore_roundTripsHistory(tmp_path: Path):
    store = JsonFileStore(tmp_path)
    assert store.load("s1") == []

    original = [_msg("user", "the entrypoint is cli.py"), _msg("assistant", "noted")]
    store.save("s1", original)

    loaded = store.load("s1")
    assert loaded == original


def test_persistence_survivesNewStoreInstance(tmp_path: Path):
    # Save with one store instance, recover with a brand-new instance using the
    # same session_id -> long-term memory survives process boundaries.
    writer = JsonFileStore(tmp_path)
    writer.save("chat-42", [_msg("user", "remember: db is postgres")])

    reader = JsonFileStore(tmp_path)
    recovered = reader.load("chat-42")
    assert recovered[0]["content"][0]["text"] == "remember: db is postgres"


def test_jsonFileStore_isolatesSessionsById(tmp_path: Path):
    store = JsonFileStore(tmp_path)
    store.save("a", [_msg("user", "fact A")])
    store.save("b", [_msg("user", "fact B")])
    assert store.load("a")[0]["content"][0]["text"] == "fact A"
    assert store.load("b")[0]["content"][0]["text"] == "fact B"


def test_jsonFileStore_rejectsUnsafeSessionId(tmp_path: Path):
    store = JsonFileStore(tmp_path)
    with pytest.raises(ValueError):
        store.save("../escape", [_msg("user", "x")])


def test_jsonFileStore_satisfiesProtocol(tmp_path: Path):
    assert isinstance(JsonFileStore(tmp_path), MemoryStore)


def test_buildStore_json_and_unimplementedBackends(tmp_path: Path):
    assert isinstance(buildStore("json", baseDir=tmp_path), JsonFileStore)
    for backend in ("sqlite", "dynamodb", "redis"):
        with pytest.raises(ValueError):
            buildStore(backend, baseDir=tmp_path)


def test_sanitizeSessionId_stripsUnsafeChars():
    assert sanitizeSessionId("Live Ep.2 / demo") == "Live-Ep.2-demo"
    assert sanitizeSessionId("../../etc") == "etc"
    assert sanitizeSessionId("   ") == "default"


# --- Multi-turn recall ------------------------------------------------------

def test_multiTurn_recallAcrossRuns(tmp_path: Path):
    # Turn 1 (run 1): a fact is stated and persisted.
    manager1 = SessionManager(JsonFileStore(tmp_path), strategy="truncate", maxTokens=10_000)
    manager1.appendExchange(
        "proj",
        _msg("user", "The entrypoint is cli.py."),
        _msg("assistant", "Understood, the entrypoint is cli.py."),
    )

    # Turn 2 (a fresh run / fresh manager): the fact must be recoverable without
    # being repeated by the user.
    manager2 = SessionManager(JsonFileStore(tmp_path), strategy="truncate", maxTokens=10_000)
    history = manager2.loadHistory("proj")
    allText = " ".join(
        block["text"]
        for message in history
        for block in message["content"]
        if "text" in block
    )
    assert "cli.py" in allText


# --- Pruning: truncate ------------------------------------------------------

def test_prune_truncate_keepsRecentDropsOld():
    # 10 messages of ~100 tokens each = ~1000 tokens.
    messages = [_msg("user", "X" * 400) for _ in range(10)]
    assert countTokens(messages) == pytest.approx(1000, rel=0.2)

    pruned = prune(messages, strategy="truncate", maxTokens=250)
    assert countTokens(pruned) <= 250
    # The kept messages are the most recent ones (tail of the list).
    assert pruned == messages[-len(pruned):]


def test_prune_noOpWhenUnderBudget():
    messages = [_msg("user", "short"), _msg("assistant", "reply")]
    pruned = prune(messages, strategy="truncate", maxTokens=10_000)
    assert pruned == messages


def test_prune_unknownStrategyRaises():
    with pytest.raises(ValueError):
        prune([_msg("user", "x" * 5000)], strategy="bogus", maxTokens=1)  # type: ignore[arg-type]


def test_prune_summarizeRequiresSummarizer():
    big = [_msg("user", "x" * 400) for _ in range(10)]
    with pytest.raises(ValueError):
        prune(big, strategy="summarize", maxTokens=100)


# --- Pruning: summarize (deterministic, injected summarizer) ----------------

def test_prune_summarize_preservesKeyFactsAndRecent():
    # Old context contains a critical fact; recent messages are separate.
    old = [
        _msg("user", "The entrypoint is cli.py. " * 20),
        _msg("assistant", "Acknowledged. " * 20),
        _msg("user", "The database is postgres. " * 20),
    ]
    recent = [
        _msg("user", "What did I say the entrypoint was?"),
        _msg("assistant", "placeholder"),
    ]
    messages = old + recent

    captured = {}

    def fakeSummarizer(oldMessages):
        captured["count"] = len(oldMessages)
        # A faithful summary preserves the key facts.
        return "Key facts: entrypoint is cli.py; database is postgres."

    pruned = prune(
        messages,
        strategy="summarize",
        maxTokens=120,
        summarizer=fakeSummarizer,
        preserveRecentMessages=2,
    )

    # First message is the summary; it preserves the critical fact.
    summaryText = pruned[0]["content"][0]["text"]
    assert "[Summary of earlier conversation]" in summaryText
    assert "cli.py" in summaryText
    # The most recent messages survive intact.
    assert pruned[-2:] == recent
    # Something old was actually summarized.
    assert captured["count"] >= 1
    # And the result is smaller than the original.
    assert countTokens(pruned) < countTokens(messages)


def test_sessionManager_summarizeStrategy_prunesOnLoad(tmp_path: Path):
    def fakeSummarizer(oldMessages):
        return "Summary: entrypoint is cli.py."

    store = JsonFileStore(tmp_path)
    # Persist a long history (full history is stored unpruned).
    longHistory = [_msg("user", "The entrypoint is cli.py. " * 20)]
    longHistory += [_msg("assistant", "ok " * 40) for _ in range(6)]
    store.save("big", longHistory)

    manager = SessionManager(
        store,
        strategy="summarize",
        maxTokens=150,
        summarizer=fakeSummarizer,
        preserveRecentMessages=2,
    )
    view = manager.loadHistory("big")
    # The model-facing view is pruned to budget...
    assert countTokens(view) <= 200
    # ...but the persisted history remains complete (long-term memory intact).
    assert len(store.load("big")) == len(longHistory)


# --- Shared state persistence ----------------------------------------------

def test_state_snapshotRestoreRoundTrip(tmp_path: Path):
    state.clearResults("/tmp/repoX")
    state.storeResult("/tmp/repoX", "quality", {"health_score": 91})
    statePath = tmp_path / "state.json"
    state.saveTo(statePath)

    state.clearResults("/tmp/repoX")
    assert state.getResult("/tmp/repoX", "quality") is None

    state.loadFrom(statePath)
    assert state.getResult("/tmp/repoX", "quality") == {"health_score": 91}
