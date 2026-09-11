"""Tests for the service agent wiring (Episode 3).

Deterministic and offline: no Bedrock, no Supabase. Verifies the service system
prompt makes code search the primary tool (RAG) and forbids re-ingesting the
repo, and that ``buildServiceAgent`` wires that prompt with streaming settings
(callback_handler=None), the native session/conversation managers, and
``search_code`` among its tools.
"""

from __future__ import annotations

import code_copilot.agent as agent_module
from code_copilot.agent import SERVICE_SYSTEM_PROMPT, buildServiceAgent
from code_copilot.tools import ALL_TOOLS


def _toolNames(tools) -> set[str]:
    names = set()
    for tool in tools:
        name = (
            getattr(tool, "tool_name", None)
            or getattr(tool, "__name__", None)
            or ""
        )
        names.add(name)
    return names


def test_service_prompt_prioritizes_search_code():
    prompt = SERVICE_SYSTEM_PROMPT.lower()
    # search_code is presented as the primary tool.
    assert "search_code" in prompt
    assert "primary tool" in prompt


def test_service_prompt_forbids_reingesting():
    prompt = SERVICE_SYSTEM_PROMPT
    # It must tell the model NOT to ingest/clone again (the repo is indexed).
    assert "ALREADY been indexed" in prompt or "ALREADY indexed" in prompt
    assert "Do NOT call ingest_repository" in prompt


def test_search_code_registered_in_all_tools():
    assert "search_code" in _toolNames(ALL_TOOLS)


def test_build_service_agent_wiring(monkeypatch):
    """buildServiceAgent wires the service prompt + streaming settings.

    ``strands.Agent`` is replaced with a capture stub so no model/session I/O
    happens; we assert on the kwargs the builder passes.
    """
    captured = {}

    class _CaptureAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    # Avoid touching Bedrock and the real Agent.
    monkeypatch.setattr(agent_module, "buildBedrockModel", lambda **_: object())
    monkeypatch.setattr("strands.Agent", _CaptureAgent)

    sentinelSession = object()
    sentinelConversation = object()
    buildServiceAgent(
        sessionManager=sentinelSession, conversationManager=sentinelConversation
    )

    assert captured["system_prompt"] is SERVICE_SYSTEM_PROMPT
    assert captured["callback_handler"] is None  # streaming over the wire
    assert captured["session_manager"] is sentinelSession
    assert captured["conversation_manager"] is sentinelConversation
    assert "search_code" in _toolNames(captured["tools"])