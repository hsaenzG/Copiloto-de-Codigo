"""Tests for the search_code tool: injection, citation, and degradation."""

from __future__ import annotations

from code_copilot.tools.search import get_retriever, search_code, set_retriever


class _FakeDoc:
    def __init__(self, content: str, metadata: dict) -> None:
        self.page_content = content
        self.metadata = metadata


class _FakeRetriever:
    def __init__(self, docs: list[_FakeDoc]) -> None:
        self._docs = docs
        self.lastQuery: str | None = None

    def invoke(self, query: str):
        self.lastQuery = query
        return self._docs


def _call(**kwargs):
    underlying = getattr(search_code, "original", None) or getattr(
        search_code, "__wrapped__", None
    ) or search_code
    return underlying(**kwargs)


def test_search_code_degrades_when_no_retriever():
    set_retriever(None)
    result = _call(query="anything")
    assert "not available" in result.lower()


def test_search_code_cites_path_and_symbol():
    retriever = _FakeRetriever(
        [
            _FakeDoc("def authenticate(): ...", {"path": "auth.py", "symbol": "authenticate"}),
            _FakeDoc("x = 1", {"path": "misc.py"}),
        ]
    )
    set_retriever(retriever)
    try:
        result = _call(query="where is auth handled?", k=5)
    finally:
        set_retriever(None)

    assert retriever.lastQuery == "where is auth handled?"
    assert "auth.py :: authenticate" in result
    assert "misc.py" in result


def test_search_code_respects_k():
    docs = [_FakeDoc(f"c{i}", {"path": f"f{i}.py"}) for i in range(10)]
    set_retriever(_FakeRetriever(docs))
    try:
        result = _call(query="q", k=3)
    finally:
        set_retriever(None)
    # Only the first 3 are rendered.
    assert "[3]" in result and "[4]" not in result


def test_search_code_handles_retriever_error():
    class _Boom:
        def invoke(self, query):
            raise RuntimeError("secret-value-should-not-leak")

    set_retriever(_Boom())
    try:
        result = _call(query="q")
    finally:
        set_retriever(None)
    assert "failed" in result.lower()
    assert "secret-value-should-not-leak" not in result
