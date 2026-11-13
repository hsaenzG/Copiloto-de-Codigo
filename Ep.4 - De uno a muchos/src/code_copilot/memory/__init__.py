"""Conversational memory for the Code Copilot agent.

This module is the Episode 2 addition: it gives the copilot *memory* as an
architectural decision, not as an unbounded message buffer. It has a single
responsibility (persist, recover, and prune conversation history) and is kept
separate from the agent wiring in ``agent.py`` / ``cli.py``.

Design overview
----------------
The public surface is a tiny, stable facade so the storage backend can be
swapped later (JSON now; SQLite, DynamoDB, or Redis later) without touching the
rest of the app:

- ``MemoryStore`` -- a ``typing.Protocol`` with just ``load`` and ``save``. This
  is the seam the rest of the codebase depends on. New backends implement these
  two methods and nothing else changes.
- ``JsonFileStore`` -- the only implementation shipped in this episode. It
  persists one file tree per ``session_id`` using Strands' own
  ``SessionMessage`` serialization so we do not reinvent the (de)serialization
  of Strands ``Message`` objects.
- ``SessionManager`` -- orchestrates one turn: ``load -> prune -> (agent runs)
  -> append -> save``.
- ``prune`` -- a pure-ish pruning function with two strategies, ``"truncate"``
  (cheap sliding window) and ``"summarize"`` (one extra model call, preserves
  context). Strategy and threshold are configurable, never hardcoded.
- ``count_tokens`` -- an approximate token counter used both to decide when to
  prune and to log the cost (token delta) of each pruning event.

Scope guard: this is *conversational* memory only. It is deliberately NOT RAG
or repository indexing (Week 3), and NOT multi-agent memory (Week 4).

All repository content remains untrusted: nothing here executes repo code, and
secret values are never logged (only message roles and token counts are).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable, Literal, Protocol, runtime_checkable

from strands.types.content import Message

logger = logging.getLogger("code_copilot.memory")

# Pruning strategies exposed to configuration. Kept as a module constant so the
# CLI and tests can validate a configured value against the supported set.
PruneStrategy = Literal["truncate", "summarize"]
SUPPORTED_STRATEGIES: tuple[str, ...] = ("truncate", "summarize")

# Rough token approximation: ~4 characters per token is the widely used
# heuristic for English/code text. This is intentionally cheap and dependency
# free; it is used for *decisions and cost logging*, not for billing accuracy.
_CHARS_PER_TOKEN = 4


# --- Token accounting -------------------------------------------------------

def countMessageTokens(message: Message) -> int:
    """Approximate the token count of a single message.

    Counts characters across every text block in the message content and
    divides by an average characters-per-token ratio. Non-text content blocks
    (tool use/result payloads) are serialized to JSON so their size still
    counts toward the budget.

    Args:
        message: A Strands ``Message`` (``{"role": ..., "content": [...]}``).

    Returns:
        An approximate, non-negative token count.
    """
    contentBlocks = message.get("content") or []
    characterTotal = 0
    for block in contentBlocks:
        if isinstance(block, dict) and "text" in block and isinstance(block["text"], str):
            characterTotal += len(block["text"])
        else:
            # Tool-use / tool-result / other structured blocks: size them by
            # their serialized form so large payloads are not counted as free.
            try:
                characterTotal += len(json.dumps(block, default=str))
            except (TypeError, ValueError):
                characterTotal += len(str(block))
    return max(0, characterTotal // _CHARS_PER_TOKEN)


def countTokens(messages: list[Message]) -> int:
    """Approximate the total token count of a list of messages."""
    return sum(countMessageTokens(message) for message in messages)


# --- Pruning ----------------------------------------------------------------

# A summarizer is any callable that turns a chunk of old messages into a single
# summary string. Injecting it (instead of importing the model directly) keeps
# ``prune`` testable without Bedrock and honors the "configurable, not
# hardcoded" rule.
Summarizer = Callable[[list[Message]], str]


def _formatMessagesForSummary(messages: list[Message]) -> str:
    """Render messages as a compact ``role: text`` transcript for summarizing."""
    lines: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        texts = [
            block["text"]
            for block in (message.get("content") or [])
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        if texts:
            lines.append(f"{role}: {' '.join(texts)}")
    return "\n".join(lines)


def _summaryMessage(summaryText: str) -> Message:
    """Wrap a summary string as a user message the model can consume as context."""
    return {
        "role": "user",
        "content": [{"text": f"[Summary of earlier conversation]\n{summaryText}"}],
    }


def prune(
    messages: list[Message],
    *,
    strategy: PruneStrategy = "truncate",
    maxTokens: int = 4000,
    summarizer: Summarizer | None = None,
    preserveRecentMessages: int = 2,
) -> list[Message]:
    """Prune conversation history so it fits within a token budget.

    Two strategies, both configurable via the ``strategy`` and ``maxTokens``
    arguments (never hardcoded at the call site):

    - ``"truncate"``: sliding window. Keeps the most recent messages that fit in
      ``maxTokens`` and drops the oldest. O(n) over messages, no extra model
      call, but loses old context.
    - ``"summarize"``: summarizes the oldest messages into one compact message
      using the injected ``summarizer`` (the model), then keeps the recent tail.
      Costs one extra model call but preserves older context.

    When the history already fits in ``maxTokens`` the list is returned
    unchanged (no work, no cost). A pruning event is logged with the strategy
    and the before/after token counts so cost is visible in the demo.

    Args:
        messages: The full conversation history.
        strategy: ``"truncate"`` or ``"summarize"``.
        maxTokens: Approximate token budget for the pruned history.
        summarizer: Callable used by the ``"summarize"`` strategy. Required for
            that strategy; ignored by ``"truncate"``.
        preserveRecentMessages: Minimum number of most-recent messages to always
            keep intact (protects the latest exchange from being summarized).

    Returns:
        A new list of messages within (approximately) the token budget.

    Raises:
        ValueError: If ``strategy`` is unknown, or ``"summarize"`` is requested
            without a ``summarizer``.
    """
    if strategy not in SUPPORTED_STRATEGIES:
        raise ValueError(
            f"Unknown prune strategy {strategy!r}; expected one of {SUPPORTED_STRATEGIES}."
        )

    tokensBefore = countTokens(messages)
    if tokensBefore <= maxTokens or len(messages) <= 1:
        # Already within budget: no pruning, no cost.
        return list(messages)

    if strategy == "truncate":
        pruned = _pruneTruncate(messages, maxTokens=maxTokens)
    else:  # "summarize"
        if summarizer is None:
            raise ValueError("The 'summarize' strategy requires a summarizer callable.")
        pruned = _pruneSummarize(
            messages,
            maxTokens=maxTokens,
            summarizer=summarizer,
            preserveRecentMessages=preserveRecentMessages,
        )

    tokensAfter = countTokens(pruned)
    logger.info(
        "memory.prune strategy=%s tokens_before=%d tokens_after=%d saved=%d "
        "messages_before=%d messages_after=%d",
        strategy,
        tokensBefore,
        tokensAfter,
        tokensBefore - tokensAfter,
        len(messages),
        len(pruned),
    )
    return pruned


def _pruneTruncate(messages: list[Message], *, maxTokens: int) -> list[Message]:
    """Sliding window: keep the newest messages that fit in ``maxTokens``."""
    keptReversed: list[Message] = []
    runningTokens = 0
    for message in reversed(messages):
        messageTokens = countMessageTokens(message)
        if keptReversed and runningTokens + messageTokens > maxTokens:
            break
        keptReversed.append(message)
        runningTokens += messageTokens
    return list(reversed(keptReversed))


def _pruneSummarize(
    messages: list[Message],
    *,
    maxTokens: int,
    summarizer: Summarizer,
    preserveRecentMessages: int,
) -> list[Message]:
    """Summarize the oldest messages, keep the recent tail intact.

    The recent tail is grown from the end until it reaches ``maxTokens`` or the
    ``preserveRecentMessages`` floor, whichever keeps more context. Everything
    older is collapsed into a single summary message placed at the front.
    """
    preserveCount = max(1, preserveRecentMessages)
    preserveCount = min(preserveCount, len(messages))

    # Grow the preserved tail while it still fits the budget, so we summarize as
    # little as necessary.
    tailTokens = countTokens(messages[-preserveCount:])
    while preserveCount < len(messages):
        nextCount = preserveCount + 1
        if countTokens(messages[-nextCount:]) > maxTokens:
            break
        preserveCount = nextCount
        tailTokens = countTokens(messages[-preserveCount:])

    olderMessages = messages[:-preserveCount]
    recentMessages = messages[-preserveCount:]
    if not olderMessages:
        # Nothing to summarize (everything is "recent"); fall back to truncation
        # so we still respect the budget.
        return _pruneTruncate(messages, maxTokens=maxTokens)

    summaryText = summarizer(olderMessages).strip()
    summary = _summaryMessage(summaryText)
    return [summary, *recentMessages]


# --- Storage backend facade -------------------------------------------------

@runtime_checkable
class MemoryStore(Protocol):
    """Storage backend for conversation history, keyed by ``session_id``.

    This is the swap point for the whole memory layer. The rest of the app only
    depends on these two methods, so a new backend (SQLite, DynamoDB, Redis)
    just implements ``load``/``save`` and everything else stays the same.
    """

    def load(self, sessionId: str) -> list[Message]:
        """Return the stored history for ``sessionId`` (empty list if none)."""
        ...

    def save(self, sessionId: str, messages: list[Message]) -> None:
        """Persist ``messages`` as the full history for ``sessionId``."""
        ...


class JsonFileStore:
    """JSON file-backed ``MemoryStore``.

    Persists each session's history under ``<baseDir>/session_<session_id>/
    messages.json``. Messages are serialized with Strands' own
    ``SessionMessage`` representation, so the on-disk format matches the
    framework's and we avoid hand-rolling ``Message`` (de)serialization.

    Simple, inspectable, zero infrastructure. The base directory is
    configurable (constructor argument), never hardcoded.
    """

    def __init__(self, baseDir: str | Path) -> None:
        """Initialize the store rooted at ``baseDir`` (created on demand)."""
        self._baseDir = Path(baseDir).expanduser()

    def _sessionDir(self, sessionId: str) -> Path:
        """Return the directory for ``sessionId``, validating the id is safe."""
        if not sessionId or any(separator in sessionId for separator in ("/", "\\", "..")):
            raise ValueError(
                f"Invalid session_id {sessionId!r}: must be non-empty and free of "
                "path separators."
            )
        return self._baseDir / f"session_{sessionId}"

    def _messagesPath(self, sessionId: str) -> Path:
        return self._sessionDir(sessionId) / "messages.json"

    def load(self, sessionId: str) -> list[Message]:
        """Load and return the stored history for ``sessionId``.

        Returns an empty list when the session has never been saved. A corrupt
        or unreadable file is treated as "no history" rather than crashing a
        run; the problem is logged.
        """
        from strands.types.session import SessionMessage

        messagesPath = self._messagesPath(sessionId)
        if not messagesPath.exists():
            return []
        try:
            rawRecords = json.loads(messagesPath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as loadError:
            logger.warning(
                "memory.load could not read session=%s: %s", sessionId, loadError
            )
            return []

        messages: list[Message] = []
        for record in rawRecords:
            try:
                messages.append(SessionMessage.from_dict(record).to_message())
            except (KeyError, TypeError, ValueError) as recordError:
                logger.warning(
                    "memory.load skipping malformed record in session=%s: %s",
                    sessionId,
                    recordError,
                )
        return messages

    def save(self, sessionId: str, messages: list[Message]) -> None:
        """Persist ``messages`` as the full history for ``sessionId``.

        Writes atomically (temp file + rename) so a crash mid-write cannot leave
        a half-written history behind.
        """
        from strands.types.session import SessionMessage

        sessionDir = self._sessionDir(sessionId)
        sessionDir.mkdir(parents=True, exist_ok=True)

        records = [
            SessionMessage.from_message(message, index).to_dict()
            for index, message in enumerate(messages)
        ]
        payload = json.dumps(records, indent=2, default=str)

        messagesPath = self._messagesPath(sessionId)
        tempPath = messagesPath.with_suffix(".json.tmp")
        tempPath.write_text(payload, encoding="utf-8")
        tempPath.replace(messagesPath)


# --- Session orchestration --------------------------------------------------

class SessionManager:
    """Orchestrates one conversational turn against a ``MemoryStore``.

    A turn is: ``load(session_id) -> prune -> (the agent runs) -> append ->
    save(session_id)``. The manager owns the memory policy (which backend, which
    pruning strategy, which token budget) so the agent wiring stays thin.

    The pruning strategy and token threshold are injected (from configuration),
    never hardcoded here.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        strategy: PruneStrategy = "truncate",
        maxTokens: int = 4000,
        summarizer: Summarizer | None = None,
        preserveRecentMessages: int = 2,
    ) -> None:
        """Configure the session manager.

        Args:
            store: The backend implementing the ``MemoryStore`` protocol.
            strategy: Default pruning strategy (``"truncate"``/``"summarize"``).
            maxTokens: Token budget that triggers pruning.
            summarizer: Callable used when ``strategy == "summarize"``.
            preserveRecentMessages: Recent messages always kept intact.
        """
        if strategy not in SUPPORTED_STRATEGIES:
            raise ValueError(
                f"Unknown prune strategy {strategy!r}; expected {SUPPORTED_STRATEGIES}."
            )
        self._store = store
        self._strategy: PruneStrategy = strategy
        self._maxTokens = maxTokens
        self._summarizer = summarizer
        self._preserveRecentMessages = preserveRecentMessages

    def loadHistory(self, sessionId: str) -> list[Message]:
        """Load the persisted history for ``sessionId`` and prune it to budget.

        This is what feeds the agent at the start of a turn: the pruned history
        is the short-term (session) memory the model receives.
        """
        history = self._store.load(sessionId)
        return prune(
            history,
            strategy=self._strategy,
            maxTokens=self._maxTokens,
            summarizer=self._summarizer,
            preserveRecentMessages=self._preserveRecentMessages,
        )

    def saveHistory(self, sessionId: str, messages: list[Message]) -> None:
        """Persist the full ``messages`` history for ``sessionId``."""
        self._store.save(sessionId, list(messages))

    def appendExchange(
        self,
        sessionId: str,
        userMessage: Message,
        assistantMessage: Message,
    ) -> list[Message]:
        """Append one user/assistant exchange to the stored history and persist.

        Loads the current (unpruned) history, appends the new exchange, saves
        the result, and returns the updated history. Pruning is applied on the
        *read* path (``loadHistory``) so persisted history stays complete for
        long-term recall while the model only ever sees the pruned view.

        Returns:
            The updated, persisted history.
        """
        history = self._store.load(sessionId)
        history.append(userMessage)
        history.append(assistantMessage)
        self._store.save(sessionId, history)
        return history


# --- Config helpers ---------------------------------------------------------

def buildStore(backend: str, *, baseDir: str | Path) -> MemoryStore:
    """Create a ``MemoryStore`` for the configured ``backend``.

    Only ``"json"`` is implemented in this episode. Additional backends
    (``"sqlite"``, ``"dynamodb"``, ``"redis"``) are intentionally left as a
    one-class extension point: implement ``MemoryStore`` and register it here.

    Args:
        backend: Backend name from configuration (e.g. ``MEMORY_BACKEND``).
        baseDir: Base directory / connection root for file-based backends.

    Raises:
        ValueError: If ``backend`` is not implemented.
    """
    normalized = (backend or "json").strip().lower()
    if normalized == "json":
        return JsonFileStore(baseDir)
    raise ValueError(
        f"Memory backend {backend!r} is not implemented in this episode. "
        "Implement the MemoryStore protocol and register it in buildStore "
        "(planned: sqlite, dynamodb, redis)."
    )


_SESSION_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitizeSessionId(sessionId: str) -> str:
    """Normalize a user-provided session id into a filesystem-safe token."""
    cleaned = _SESSION_ID_SAFE.sub("-", sessionId.strip())
    # Collapse any parent-directory sequences that survived normalization so the
    # id can never reach outside its session directory.
    cleaned = cleaned.replace("..", "-").strip("-")
    return cleaned or "default"
