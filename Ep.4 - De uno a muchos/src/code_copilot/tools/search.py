"""Tool: search_code (code RAG retrieval).

Exposes the repository's vector index to the Strands agent as one more tool.
From the agent's point of view ``search_code`` is indistinguishable from
``read_file``: the agent decides *when* to search; LangChain (behind the
retriever) only decides *what* to return.

The retriever is injected once at startup via ``set_retriever(...)`` so this
module never imports LangChain or builds an index itself. That indirection is
deliberate: the retrieval backend (LangChain today, LlamaIndex tomorrow) can be
swapped without touching the agent or this tool.
"""

from __future__ import annotations

import logging
from typing import Any

from strands import tool

logger = logging.getLogger("code_copilot.tools.search")

# Process-wide retriever, injected once at startup. Kept private; callers use
# set_retriever / get_retriever.
_RETRIEVER: Any | None = None


def set_retriever(retriever: Any) -> None:
    """Inject the retriever used by ``search_code`` (call once at startup)."""
    global _RETRIEVER
    _RETRIEVER = retriever


def get_retriever() -> Any | None:
    """Return the injected retriever, or ``None`` when RAG is not configured."""
    return _RETRIEVER


def _formatDocument(index: int, document: Any) -> str:
    """Render one retrieved document with its source citation."""
    metadata = getattr(document, "metadata", {}) or {}
    path = metadata.get("path", "<unknown>")
    symbol = metadata.get("symbol")
    header = f"[{index}] {path}"
    if symbol:
        header += f" :: {symbol}"
    content = getattr(document, "page_content", "")
    return f"{header}\n{content}"


@tool
def search_code(query: str, k: int = 5) -> str:
    """Search the indexed codebase for fragments relevant to a query.

    Use this to find where something lives or how it works across the WHOLE
    repository, instead of guessing filenames or reading files one by one. Good
    for questions like "where is auth handled?", "how are requests validated?",
    or "which module builds the report?". Each result cites its source ``path``
    (and ``symbol`` when the index was built with symbol chunking) so you can
    reference it in your answer.

    Args:
        query: Natural-language description of what you are looking for.
        k: Maximum number of fragments to return (top-k by relevance).

    Returns:
        The top-k relevant fragments, each prefixed with its source path (and
        symbol), or a message when retrieval is unavailable or finds nothing.
    """
    retriever = get_retriever()
    if retriever is None:
        return (
            "Code search is not available: no index has been configured for "
            "this session."
        )

    try:
        # Current retriever API. Do NOT use get_relevant_documents (deprecated).
        documents = retriever.invoke(query)
    except Exception as searchError:  # noqa: BLE001 - degrade, never crash the agent
        logger.warning("search_code retrieval failed: %s", searchError)
        return f"Code search failed: {type(searchError).__name__}"

    if not documents:
        return f"No relevant code fragments found for: {query!r}"

    topDocuments = documents[:k]
    rendered = [
        _formatDocument(index, document)
        for index, document in enumerate(topDocuments, start=1)
    ]
    return "\n\n".join(rendered)
