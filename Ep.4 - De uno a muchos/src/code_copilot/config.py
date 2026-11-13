"""Configuration, read from the environment.

Episode 2 introduces conversational memory. Episode 3 adds three more config
groups: RAG (code indexing/retrieval), the native-SDK memory backend on
Supabase, and the HTTP service (stream timeout). Every decision (backend,
pruning strategy, token threshold, chunking strategy, index path, embeddings
model, Supabase credentials, stream timeout) is configurable through
environment variables so nothing is hardcoded at a call site. Defaults are
chosen so the copilot works out of the box.

Variables (also documented in ``.env.example``):

Episode 2 (memory):
- ``MEMORY_BACKEND``       Storage backend for history. Currently ``json``.
- ``MEMORY_DIR``           Directory where JSON session history is persisted.
- ``MEMORY_STRATEGY``      Pruning strategy: ``truncate`` or ``summarize``.
- ``MEMORY_MAX_TOKENS``    Approx. token budget that triggers pruning.
- ``MEMORY_PRESERVE_RECENT`` Recent messages always kept intact when pruning.

Episode 3 (RAG retrieval):
- ``RAG_CHUNK_STRATEGY``   ``file`` (RecursiveCharacterTextSplitter) or ``symbol``.
- ``RAG_CHUNK_SIZE``       Chunk size (characters) for the file splitter.
- ``RAG_CHUNK_OVERLAP``    Chunk overlap (characters) for the file splitter.
- ``RAG_INDEX_DIR``        Directory where the FAISS index is persisted/loaded.
- ``EMBEDDINGS_MODEL_ID``  Bedrock embeddings model id.

Episode 3 (native memory backend + HTTP service):
- ``SUPABASE_URL``         Supabase project URL for the native session backend.
- ``SUPABASE_KEY``         Supabase API key (never hardcode this).
- ``CONVERSATION_MANAGER`` ``sliding_window`` or ``summarizing`` (native pruning).
- ``CONVERSATION_WINDOW_SIZE`` Window size for the sliding-window manager.
- ``STREAM_TIMEOUT_SECONDS`` Per-request timeout for the streaming endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from code_copilot.memory import SUPPORTED_STRATEGIES, PruneStrategy


def loadDotEnv(dotEnvPath: str | Path = ".env") -> None:
    """Load simple ``KEY=VALUE`` lines from a ``.env`` file into the environment.

    Existing environment variables take precedence and are not overwritten.
    Dependency-free (no python-dotenv). Shared by the CLI and the HTTP service
    so both pick up ``.env`` the same way.
    """
    path = Path(dotEnvPath)
    if not path.exists():
        return
    for rawLine in path.read_text(encoding="utf-8").splitlines():
        line = rawLine.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keyName, _, rawValue = line.partition("=")
        keyName = keyName.strip()
        value = rawValue.strip().strip('"').strip("'")
        if keyName and keyName not in os.environ:
            os.environ[keyName] = value


# --- Episode 3 defaults ------------------------------------------------------

# RAG / retrieval. "file" is the robust default (preserves module context);
# "symbol" splits by function/class for finer-grained retrieval.
CHUNK_STRATEGY_FILE = "file"
CHUNK_STRATEGY_SYMBOL = "symbol"
SUPPORTED_CHUNK_STRATEGIES = (CHUNK_STRATEGY_FILE, CHUNK_STRATEGY_SYMBOL)
DEFAULT_CHUNK_STRATEGY = CHUNK_STRATEGY_FILE
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150
DEFAULT_INDEX_DIR = str(Path.home() / ".code_copilot" / "index")
DEFAULT_EMBEDDINGS_MODEL_ID = "amazon.titan-embed-text-v2:0"

# Native conversation manager (SDK-side pruning) for the HTTP service.
CONVERSATION_MANAGER_SLIDING = "sliding_window"
CONVERSATION_MANAGER_SUMMARIZING = "summarizing"
SUPPORTED_CONVERSATION_MANAGERS = (
    CONVERSATION_MANAGER_SLIDING,
    CONVERSATION_MANAGER_SUMMARIZING,
)
DEFAULT_CONVERSATION_MANAGER = CONVERSATION_MANAGER_SLIDING
DEFAULT_CONVERSATION_WINDOW_SIZE = 40

# HTTP service.
DEFAULT_STREAM_TIMEOUT_SECONDS = 60

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


# --- Episode 3: RAG retrieval configuration ---------------------------------


@dataclass(frozen=True)
class RagConfig:
    """Resolved RAG (indexing/retrieval) configuration.

    Every knob (chunking strategy, chunk size/overlap, where the FAISS index
    lives, which Bedrock embeddings model to use) is read from the environment
    so nothing is hardcoded at the ``knowledge.py`` call site.
    """

    chunkStrategy: str
    chunkSize: int
    chunkOverlap: int
    indexDir: str
    embeddingsModelId: str


def _readChunkStrategy(envName: str, fallback: str) -> str:
    """Read and validate the chunking strategy from the environment."""
    rawValue = (os.environ.get(envName) or "").strip().lower()
    if rawValue in SUPPORTED_CHUNK_STRATEGIES:
        return rawValue
    return fallback


def loadRagConfig() -> RagConfig:
    """Build a ``RagConfig`` from the current environment."""
    return RagConfig(
        chunkStrategy=_readChunkStrategy("RAG_CHUNK_STRATEGY", DEFAULT_CHUNK_STRATEGY),
        chunkSize=_readInt("RAG_CHUNK_SIZE", DEFAULT_CHUNK_SIZE),
        chunkOverlap=_readInt("RAG_CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP),
        indexDir=os.environ.get("RAG_INDEX_DIR") or DEFAULT_INDEX_DIR,
        embeddingsModelId=(
            os.environ.get("EMBEDDINGS_MODEL_ID") or DEFAULT_EMBEDDINGS_MODEL_ID
        ),
    )


# --- Episode 3: native-SDK memory + HTTP service configuration --------------


@dataclass(frozen=True)
class ServiceConfig:
    """Resolved configuration for the FastAPI streaming service.

    Bundles the Supabase connection (native session backend), the native
    conversation manager selection, and the per-request stream timeout. The
    Supabase key is read from the environment and never stored in code.
    """

    supabaseUrl: str | None
    supabaseKey: str | None
    conversationManager: str
    conversationWindowSize: int
    streamTimeoutSeconds: int


def _readConversationManager(envName: str, fallback: str) -> str:
    """Read and validate the native conversation manager from the environment."""
    rawValue = (os.environ.get(envName) or "").strip().lower()
    if rawValue in SUPPORTED_CONVERSATION_MANAGERS:
        return rawValue
    return fallback


def loadServiceConfig() -> ServiceConfig:
    """Build a ``ServiceConfig`` from the current environment.

    ``SUPABASE_URL`` / ``SUPABASE_KEY`` are optional here (left as ``None`` when
    unset) so importing the config never raises; the API validates their
    presence at startup and fails with a clear message if the service is run
    without them.
    """
    return ServiceConfig(
        supabaseUrl=os.environ.get("SUPABASE_URL") or None,
        supabaseKey=os.environ.get("SUPABASE_KEY") or None,
        conversationManager=_readConversationManager(
            "CONVERSATION_MANAGER", DEFAULT_CONVERSATION_MANAGER
        ),
        conversationWindowSize=_readInt(
            "CONVERSATION_WINDOW_SIZE", DEFAULT_CONVERSATION_WINDOW_SIZE
        ),
        streamTimeoutSeconds=_readInt(
            "STREAM_TIMEOUT_SECONDS", DEFAULT_STREAM_TIMEOUT_SECONDS
        ),
    )
