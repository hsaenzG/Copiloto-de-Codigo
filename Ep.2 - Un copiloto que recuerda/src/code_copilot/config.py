"""Memory configuration, read from the environment.

Episode 2 introduces conversational memory. Every memory decision (which
backend, which pruning strategy, which token threshold, where to persist) is
configurable through environment variables so nothing is hardcoded at a call
site. Defaults are chosen so the copilot works out of the box.

Variables (also documented in ``.env.example``):

- ``MEMORY_BACKEND``       Storage backend for history. Currently ``json``.
- ``MEMORY_DIR``           Directory where JSON session history is persisted.
- ``MEMORY_STRATEGY``      Pruning strategy: ``truncate`` or ``summarize``.
- ``MEMORY_MAX_TOKENS``    Approx. token budget that triggers pruning.
- ``MEMORY_PRESERVE_RECENT`` Recent messages always kept intact when pruning.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from code_copilot.memory import SUPPORTED_STRATEGIES, PruneStrategy

# Defaults. A local, user-private directory mirrors how Strands' own
# FileSessionManager defaults under the home directory.
DEFAULT_MEMORY_BACKEND = "json"
DEFAULT_MEMORY_DIR = str(Path.home() / ".code_copilot" / "sessions")
DEFAULT_MEMORY_STRATEGY: PruneStrategy = "truncate"
DEFAULT_MEMORY_MAX_TOKENS = 4000
DEFAULT_MEMORY_PRESERVE_RECENT = 2
DEFAULT_SESSION_ID = "default"


@dataclass(frozen=True)
class MemoryConfig:
    """Resolved memory configuration for one CLI run."""

    backend: str
    directory: str
    strategy: PruneStrategy
    maxTokens: int
    preserveRecentMessages: int


def _readInt(envName: str, fallback: int) -> int:
    """Read a positive int from the environment, falling back on any error."""
    rawValue = os.environ.get(envName)
    if rawValue is None:
        return fallback
    try:
        parsed = int(rawValue)
    except ValueError:
        return fallback
    return parsed if parsed > 0 else fallback


def _readStrategy(envName: str, fallback: PruneStrategy) -> PruneStrategy:
    """Read and validate the pruning strategy from the environment."""
    rawValue = (os.environ.get(envName) or "").strip().lower()
    if rawValue in SUPPORTED_STRATEGIES:
        return rawValue  # type: ignore[return-value]
    return fallback


def loadMemoryConfig() -> MemoryConfig:
    """Build a ``MemoryConfig`` from the current environment."""
    return MemoryConfig(
        backend=(os.environ.get("MEMORY_BACKEND") or DEFAULT_MEMORY_BACKEND).strip().lower(),
        directory=os.environ.get("MEMORY_DIR") or DEFAULT_MEMORY_DIR,
        strategy=_readStrategy("MEMORY_STRATEGY", DEFAULT_MEMORY_STRATEGY),
        maxTokens=_readInt("MEMORY_MAX_TOKENS", DEFAULT_MEMORY_MAX_TOKENS),
        preserveRecentMessages=_readInt(
            "MEMORY_PRESERVE_RECENT", DEFAULT_MEMORY_PRESERVE_RECENT
        ),
    )


def _bedrockSummarizer(oldMessages) -> str:
    """Summarize old messages with Bedrock Nova Pro.

    Imported lazily and defined here (not in ``memory.py``) so the memory layer
    stays testable without Bedrock. Used only when the pruning strategy is
    ``summarize``.
    """
    from code_copilot.memory import _formatMessagesForSummary
    from code_copilot.model import runModelPrompt

    transcript = _formatMessagesForSummary(oldMessages)
    prompt = (
        "Summarize the following conversation so a code assistant can keep "
        "working with full context. Preserve concrete facts, decisions, file "
        "paths and identifiers. Do not invent details. Be concise.\n\n"
        f"{transcript}"
    )
    return runModelPrompt(prompt, temperature=0.1)


def buildSessionManager(config: MemoryConfig):
    """Construct a configured ``SessionManager`` from a ``MemoryConfig``.

    Wires the storage backend and pruning policy. The summarizer (a Bedrock
    call) is only attached when the strategy is ``summarize``, so a ``truncate``
    run never touches the model.
    """
    from code_copilot.memory import SessionManager, buildStore

    store = buildStore(config.backend, baseDir=config.directory)
    summarizer = _bedrockSummarizer if config.strategy == "summarize" else None
    return SessionManager(
        store,
        strategy=config.strategy,
        maxTokens=config.maxTokens,
        summarizer=summarizer,
        preserveRecentMessages=config.preserveRecentMessages,
    )
