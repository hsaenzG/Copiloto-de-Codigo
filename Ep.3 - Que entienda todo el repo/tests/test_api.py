"""Tests for the FastAPI streaming endpoint.

Deterministic and offline: the real agent is replaced via
``app.state.agentFactory`` with fakes that drive stream_async, so no Bedrock,
Supabase, or network is involved. Verifies streaming, the timeout, and
in-stream error degradation (no hard 500 once tokens have started).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from code_copilot.api import app


class _FakeStreamingAgent:
    """Yields the given text chunks as stream_async events with a 'data' key."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def stream_async(self, message: str):
        for chunk in self._chunks:
            # Mimic the SDK event shape: text arrives under the 'data' key,
            # other event types (no 'data') must be ignored by the endpoint.
            yield {"event": "start"}
            yield {"data": chunk}


class _SlowAgent:
    """Sleeps longer than the timeout to trigger the timeout branch."""

    async def stream_async(self, message: str):
        yield {"data": "starting..."}
        await asyncio.sleep(5)
        yield {"data": "never reached"}


class _FailingAgent:
    """Emits a token, then raises to trigger in-stream error degradation."""

    async def stream_async(self, message: str):
        yield {"data": "partial answer "}
        raise RuntimeError("boom-secret-should-not-leak")


def _clientWith(factory) -> TestClient:
    app.state.agentFactory = factory
    app.state.skipStartupIndex = True
    return TestClient(app)


def _reset():
    for attr in (
        "agentFactory",
        "retrieverFactory",
        "pipelineFactory",
        "skipStartupIndex",
    ):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def test_chat_streams_text_chunks():
    client = _clientWith(lambda sessionId, config: _FakeStreamingAgent(["hello ", "world"]))
    try:
        response = client.post("/chat", json={"session_id": "s1", "message": "hi"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.text == "hello world"
    finally:
        _reset()


def test_chat_times_out_inside_stream(monkeypatch):
    monkeypatch.setenv("STREAM_TIMEOUT_SECONDS", "1")
    client = _clientWith(lambda sessionId, config: _SlowAgent())
    try:
        response = client.post("/chat", json={"session_id": "s2", "message": "hi"})
        assert response.status_code == 200
        assert "starting..." in response.text
        assert "[error]" in response.text
        assert "timed out" in response.text
    finally:
        _reset()


def test_chat_error_does_not_break_stream():
    client = _clientWith(lambda sessionId, config: _FailingAgent())
    try:
        response = client.post("/chat", json={"session_id": "s3", "message": "hi"})
        # Not a hard 500: tokens already started, so the error is in-stream.
        assert response.status_code == 200
        assert "partial answer" in response.text
        assert "[error] RuntimeError" in response.text
        # The error message never leaks the exception's payload.
        assert "boom-secret-should-not-leak" not in response.text
    finally:
        _reset()


def test_health():
    _reset()
    app.state.skipStartupIndex = True
    client = TestClient(app)
    try:
        assert client.get("/health").json() == {"status": "ok"}
    finally:
        _reset()


def test_index_injects_retriever_and_search_uses_it():
    """POST /index builds a retriever and wires it into search_code."""
    from code_copilot.tools.search import get_retriever

    class _FakeRetriever:
        def invoke(self, query):
            return []

    app.state.skipStartupIndex = True
    app.state.retrieverFactory = lambda repo: _FakeRetriever()
    client = TestClient(app)
    try:
        response = client.post("/index", json={"repo": "/some/repo"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "indexed"
        assert body["repo"] == "/some/repo"
        assert "analysis" in body  # analysis field is always present
        # The retriever was injected process-wide for search_code / chat.
        assert isinstance(get_retriever(), _FakeRetriever)
    finally:
        _reset()
        from code_copilot.tools.search import set_retriever

        set_retriever(None)


def test_index_returns_analysis_summary():
    """The retriever factory can return (retriever, analysis); it's surfaced."""
    from code_copilot.tools.search import set_retriever

    class _FakeRetriever:
        def invoke(self, query):
            return []

    fakeAnalysis = {
        "source": "/some/repo",
        "file_count": 3,
        "languages": {"Python": 2, "JavaScript": 1},
        "chunk_strategy": "symbol",
        "chunk_count": 7,
    }
    app.state.skipStartupIndex = True
    app.state.retrieverFactory = lambda repo: (_FakeRetriever(), fakeAnalysis)
    client = TestClient(app)
    try:
        response = client.post("/index", json={"repo": "/some/repo"})
        assert response.status_code == 200
        assert response.json()["analysis"] == fakeAnalysis
    finally:
        _reset()
        set_retriever(None)


def test_analyze_returns_summary_and_sections():
    """POST /analyze runs the pipeline and returns headline findings."""
    pipelineResult = {
        "ingest": {"repo_path": "/some/repo"},
        "structure": {"symbol_count": 12, "files_analyzed": 3},
        "sast": {"finding_count": 2},
        "dependencies": {"vulnerability_count": 1},
        "secrets": {"finding_count": 0},
        "quality": {"health_score": 74},
        "report": {"written_to": "reports/x.md"},
    }
    app.state.skipStartupIndex = True
    app.state.pipelineFactory = lambda repo, reportPath: pipelineResult
    client = TestClient(app)
    try:
        response = client.post("/analyze", json={"repo": "/some/repo"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "analyzed"
        assert body["summary"] == {
            "health_score": 74,
            "sast_findings": 2,
            "dependency_vulnerabilities": 1,
            "potential_secrets": 0,
            "report_written_to": "reports/x.md",
        }
        assert body["structure"]["symbol_count"] == 12
    finally:
        _reset()


def test_analyze_maps_pipeline_error_to_400():
    app.state.skipStartupIndex = True
    app.state.pipelineFactory = lambda repo, reportPath: {"error": "git clone failed: not found"}
    client = TestClient(app)
    try:
        response = client.post("/analyze", json={"repo": "https://github.com/x/nope"})
        assert response.status_code == 400
        assert "git clone failed" in response.json()["detail"]
    finally:
        _reset()


def test_index_reports_no_source_files_as_400():
    def _boom(repo):
        raise ValueError("No indexable source files found under /empty")

    app.state.skipStartupIndex = True
    app.state.retrieverFactory = _boom
    client = TestClient(app)
    try:
        response = client.post("/index", json={"repo": "/empty"})
        assert response.status_code == 400
        assert "No indexable source files" in response.json()["detail"]
    finally:
        _reset()
