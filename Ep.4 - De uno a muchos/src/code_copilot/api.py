"""FastAPI streaming service for the copilot.

Exposes the Strands agent behind ``POST /chat`` with a streaming response. The
agent uses the native-SDK memory (Supabase-backed ``RepositorySessionManager``)
keyed by ``session_id``: it restores the history and persists each message on
its own, so this endpoint never manages memory by hand.

Error discipline (same graceful-degradation principle as Ep.1, now at the HTTP
layer):
- The whole stream is bounded by a configurable timeout.
- Once tokens have started flowing (a 200 is in progress) a failure does NOT
  become a hard 500: the error is caught, a short error event is emitted inside
  the stream, and the stream closes cleanly. The message states the failure
  type only -- never a secret value or a full stack trace.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from code_copilot.config import ServiceConfig, loadDotEnv, loadServiceConfig
from code_copilot.memory import sanitizeSessionId
from code_copilot.tools.search import set_retriever

# Load .env at import so uvicorn picks up AWS/Supabase/RAG settings without the
# CLI (existing environment variables still win).
loadDotEnv()

logger = logging.getLogger("code_copilot.api")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Wire a persisted index into ``search_code`` at startup, when one exists.

    Lets ``/chat`` search immediately after a restart without reindexing. If no
    index has been built yet (or loading fails), the service still starts;
    ``search_code`` simply reports that no index is configured until ``/index``
    is called.
    """
    if not getattr(app.state, "skipStartupIndex", False):
        try:
            from code_copilot.knowledge import loadIndex

            index = loadIndex()
            if index is not None:
                set_retriever(index.as_retriever())
                logger.info("api.startup loaded persisted code index")
        except Exception as loadError:  # noqa: BLE001 - never block startup on RAG
            logger.warning(
                "api.startup could not load persisted index: %s",
                type(loadError).__name__,
            )
    yield


app = FastAPI(title="Code Copilot", version="0.3.0", lifespan=_lifespan)


class ChatRequest(BaseModel):
    """Request body for ``POST /chat``."""

    session_id: str
    message: str


class IndexRequest(BaseModel):
    """Request body for ``POST /index``.

    ``repo`` is a local directory path OR a GitHub URL (URLs are shallow
    cloned). After indexing, the retriever is injected so ``search_code`` (and
    therefore ``/chat``) can search this repository.
    """

    repo: str


class AnalyzeRequest(BaseModel):
    """Request body for ``POST /analyze``.

    ``repo`` is a local directory path OR a GitHub URL. Runs the full static
    review pipeline (Ep.1): ingest, structure, SAST, dependencies, secrets, and
    quality, then assembles a Markdown report.
    """

    repo: str
    report_path: str = ""


def _buildAgentForSession(sessionId: str, config: ServiceConfig):
    """Build a service agent wired to Supabase memory for ``sessionId``.

    Imported lazily so importing this module (e.g. in tests) does not require
    Supabase/Bedrock. Overridable in tests via ``app.state.agentFactory``.
    """
    factory = getattr(app.state, "agentFactory", None)
    if factory is not None:
        return factory(sessionId, config)

    from code_copilot.agent import buildServiceAgent
    from code_copilot.memory.native import (
        buildConversationManager,
        buildSessionManager,
        buildSupabaseClient,
    )

    client = buildSupabaseClient(config)
    sessionManager = buildSessionManager(sessionId, client)
    conversationManager = buildConversationManager(config)
    return buildServiceAgent(
        sessionManager=sessionManager, conversationManager=conversationManager
    )


async def _streamAgent(agent, message: str, timeoutSeconds: int) -> AsyncIterator[str]:
    """Yield the agent's text chunks, bounded by a timeout and error-safe.

    Only text chunks are forwarded: when an event carries the ``data`` key its
    value is yielded. A timeout or any other failure is turned into a single
    error line inside the stream rather than aborting a response that already
    started sending tokens.
    """
    try:
        # asyncio.timeout() requires Python 3.11+ (this project targets 3.11+);
        # on 3.10 this would be asyncio.wait_for.
        async with asyncio.timeout(timeoutSeconds):
            async for event in agent.stream_async(message):
                if "data" in event:
                    yield event["data"]
    except asyncio.TimeoutError:
        logger.warning("chat stream timed out after %ss", timeoutSeconds)
        yield f"\n[error] response timed out after {timeoutSeconds}s"
    except Exception as streamError:  # noqa: BLE001 - degrade in-stream, no 500 mid-tokens
        # Report the failure TYPE only: never a secret value or full stack.
        logger.warning("chat stream failed: %s", type(streamError).__name__)
        yield f"\n[error] {type(streamError).__name__}"


def _indexRepository(repo: str) -> dict:
    """Index ``repo``, inject the retriever into ``search_code``, return analysis.

    Overridable in tests via ``app.state.retrieverFactory`` (a callable that
    returns either a retriever or a ``(retriever, analysis)`` tuple).
    """
    factory = getattr(app.state, "retrieverFactory", None)
    if factory is not None:
        result = factory(repo)
        retriever, analysis = result if isinstance(result, tuple) else (result, {})
        set_retriever(retriever)
        return analysis

    from code_copilot.knowledge import buildRetrieverWithAnalysis

    # Always rebuild so a newly requested repo replaces any old on-disk index
    # instead of silently reusing it.
    retriever, analysis = buildRetrieverWithAnalysis(repo)
    set_retriever(retriever)
    return analysis


@app.post("/index")
def index_repo(request: IndexRequest) -> dict:
    """Index a repository (local path or GitHub URL) for code search.

    Clones the repo when a URL is given, builds the FAISS index (persisted to
    ``RAG_INDEX_DIR``), injects the retriever so ``/chat`` can answer questions
    about the code, and returns an ``analysis`` describing what was processed
    (source, resolved path, languages, file/size counts, chunking strategy and
    chunk stats). Runs synchronously: indexing is CPU/IO bound and the caller
    waits for it to finish.
    """
    try:
        analysis = _indexRepository(request.repo)
    except ValueError as indexError:
        # e.g. no indexable source files under the given path.
        raise HTTPException(status_code=400, detail=str(indexError)) from None
    except Exception as buildError:  # noqa: BLE001 - surface type only, no stack
        logger.exception("indexing failed")
        raise HTTPException(
            status_code=502,
            detail=f"indexing failed: {type(buildError).__name__}",
        ) from None
    return {"status": "indexed", "repo": request.repo, "analysis": analysis}


def _summarizePipeline(result: dict) -> dict:
    """Extract the headline findings from a full pipeline result."""
    quality = result.get("quality", {}) or {}
    sast = result.get("sast", {}) or {}
    dependencies = result.get("dependencies", {}) or {}
    secrets = result.get("secrets", {}) or {}
    report = result.get("report", {}) or {}
    return {
        "health_score": quality.get("health_score"),
        "sast_findings": sast.get("finding_count", 0),
        "dependency_vulnerabilities": dependencies.get("vulnerability_count", 0),
        "potential_secrets": secrets.get("finding_count", 0),
        "report_written_to": report.get("written_to"),
    }


def _runAnalysis(repo: str, reportPath: str) -> dict:
    """Run the full static pipeline over ``repo``.

    Overridable in tests via ``app.state.pipelineFactory``.
    """
    factory = getattr(app.state, "pipelineFactory", None)
    if factory is not None:
        return factory(repo, reportPath)

    from code_copilot.pipeline import runPipeline

    return runPipeline(repo, reportPath)


@app.post("/analyze")
def analyze_repo(request: AnalyzeRequest) -> dict:
    """Run the full static review (Ep.1) over a repo and return the findings.

    This is the heavier, LLM-free analysis: it clones/validates the repo, maps
    its structure, runs SAST / dependency / secret scans and a maintainability
    assessment, and assembles a Markdown report. Separate from ``/index`` (which
    only builds the retrieval index) because it is more expensive. Runs
    synchronously; the caller waits for it to finish.
    """
    try:
        result = _runAnalysis(request.repo, request.report_path)
    except Exception as analyzeError:  # noqa: BLE001 - surface type only, no stack
        logger.exception("analysis failed")
        raise HTTPException(
            status_code=502,
            detail=f"analysis failed: {type(analyzeError).__name__}",
        ) from None

    if result.get("error"):
        # e.g. clone failed or path not found: a client-fixable input problem.
        raise HTTPException(status_code=400, detail=result["error"])

    return {
        "status": "analyzed",
        "repo": request.repo,
        "summary": _summarizePipeline(result),
        "structure": result.get("structure"),
        "sast": result.get("sast"),
        "dependencies": result.get("dependencies"),
        "secrets": result.get("secrets"),
        "quality": result.get("quality"),
    }


@app.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Chat with the copilot over an indexed repo, streaming the response.

    The native session manager restores history for ``session_id`` and persists
    each message on its own; this handler does not touch memory directly.
    """
    config = loadServiceConfig()
    sessionId = sanitizeSessionId(request.session_id)
    try:
        # Setup happens BEFORE any token is sent, so a failure here is a clean
        # status code (not an in-stream error). Report the reason, never a stack.
        agent = _buildAgentForSession(sessionId, config)
    except ValueError as configError:
        raise HTTPException(status_code=503, detail=str(configError)) from None
    except Exception as buildError:  # noqa: BLE001 - surface type only to client
        # Log the full error server-side for diagnosis; the client only gets the
        # type (never a secret value or a full stack).
        logger.exception("agent setup failed")
        raise HTTPException(
            status_code=503, detail=f"agent setup failed: {type(buildError).__name__}"
        ) from None
    return StreamingResponse(
        _streamAgent(agent, request.message, config.streamTimeoutSeconds),
        media_type="text/event-stream",
    )


@app.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}
