"""In-memory test doubles used across the Episode 3 test suite.

These let the tests exercise real code paths (the SupabaseSessionRepository, the
retriever tool, the streaming endpoint) deterministically and offline, without
hitting Supabase, Bedrock, or the network.
"""

from __future__ import annotations

import hashlib
from typing import Any


# --- Fake Supabase client ---------------------------------------------------


class _FakeResponse:
    """Mimics the ``.data`` attribute of a supabase-py execute() result."""

    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _FakeQuery:
    """A tiny fluent query over an in-memory list of rows.

    Supports the subset of the supabase-py builder that
    ``SupabaseSessionRepository`` uses: select/eq/order/range/execute and
    insert/upsert/execute.
    """

    def __init__(self, table: "_FakeTable") -> None:
        self._table = table
        self._filters: list[tuple[str, Any]] = []
        self._orderColumn: str | None = None
        self._range: tuple[int, int] | None = None
        self._pendingWrite: tuple[str, dict] | None = None
        self._isSelect = False

    # reads
    def select(self, _columns: str = "*") -> "_FakeQuery":
        self._isSelect = True
        return self

    def eq(self, column: str, value: Any) -> "_FakeQuery":
        self._filters.append((column, value))
        return self

    def order(self, column: str) -> "_FakeQuery":
        self._orderColumn = column
        return self

    def range(self, start: int, end: int) -> "_FakeQuery":
        self._range = (start, end)
        return self

    # writes
    def insert(self, row: dict) -> "_FakeQuery":
        self._pendingWrite = ("insert", row)
        return self

    def upsert(self, row: dict) -> "_FakeQuery":
        self._pendingWrite = ("upsert", row)
        return self

    def execute(self) -> _FakeResponse:
        if self._pendingWrite is not None:
            return self._runWrite()
        return self._runSelect()

    def _matches(self, row: dict) -> bool:
        return all(row.get(column) == value for column, value in self._filters)

    def _runSelect(self) -> _FakeResponse:
        rows = [dict(row) for row in self._table.rows if self._matches(row)]
        if self._orderColumn is not None:
            rows.sort(key=lambda row: row.get(self._orderColumn))
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        return _FakeResponse(rows)

    def _runWrite(self) -> _FakeResponse:
        operation, row = self._pendingWrite  # type: ignore[misc]
        keyColumns = self._table.primaryKey
        existingIndex = self._table.findByKey(row)
        if existingIndex is not None:
            if operation == "insert":
                raise ValueError(
                    f"duplicate key on {keyColumns} in {self._table.name!r}"
                )
            self._table.rows[existingIndex] = dict(row)
        else:
            self._table.rows.append(dict(row))
        return _FakeResponse([dict(row)])


class _FakeTable:
    """An in-memory table with a (possibly composite) primary key."""

    def __init__(self, name: str, primaryKey: tuple[str, ...]) -> None:
        self.name = name
        self.primaryKey = primaryKey
        self.rows: list[dict] = []

    def findByKey(self, row: dict) -> int | None:
        for index, existing in enumerate(self.rows):
            if all(existing.get(column) == row.get(column) for column in self.primaryKey):
                return index
        return None

    # query entrypoints (each call starts a fresh builder)
    def select(self, columns: str = "*") -> _FakeQuery:
        return _FakeQuery(self).select(columns)

    def insert(self, row: dict) -> _FakeQuery:
        return _FakeQuery(self).insert(row)

    def upsert(self, row: dict) -> _FakeQuery:
        return _FakeQuery(self).upsert(row)


class FakeSupabaseClient:
    """A minimal in-memory stand-in for ``supabase.Client``.

    Knows the two tables the repository uses and their primary keys, so upsert
    and duplicate-key semantics behave like PostgreSQL.
    """

    def __init__(self) -> None:
        self._tables = {
            "sessions": _FakeTable("sessions", ("session_id",)),
            "messages": _FakeTable("messages", ("session_id", "agent_id", "message_id")),
        }

    def table(self, name: str) -> _FakeTable:
        return self._tables[name]


# --- Fake embeddings (deterministic, offline) -------------------------------


try:  # Inherit the LangChain Embeddings interface when available.
    from langchain_core.embeddings import Embeddings as _EmbeddingsBase
except Exception:  # pragma: no cover - langchain not installed
    _EmbeddingsBase = object


class HashingEmbeddings(_EmbeddingsBase):
    """Deterministic bag-of-words embeddings with no network or model call.

    Maps text to a fixed-size vector by hashing tokens into buckets. Good enough
    for reproducible recall@k tests: fragments that share vocabulary with the
    query land near it, and results are stable across runs.
    """

    def __init__(self, dimensions: int = 256) -> None:
        self._dimensions = dimensions

    def _bucket(self, token: str) -> int:
        # Stable across processes (unlike built-in hash(), which is salted).
        digest = hashlib.md5(token.encode("utf-8")).hexdigest()
        return int(digest, 16) % self._dimensions

    def _embedText(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in text.lower().split():
            vector[self._bucket(token)] += 1.0
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embedText(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embedText(text)
