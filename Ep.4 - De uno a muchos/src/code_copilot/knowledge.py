"""Code RAG: ingest a repository into a local vector index and retrieve by
relevance.

Episode 3 gives the copilot an understanding of the *whole* repository, not
just the files a tool happens to open one at a time. This module has a single
responsibility: index the codebase and expose a retriever. It does NOT wire the
agent.

Design boundaries (kept explicit on purpose):

- **RAG is not memory.** The conversation memory (Ep.2, keyed by ``session_id``)
  remembers the chat; this index recovers *knowledge about the repo*. They are
  two different stores, with different lifecycles. Do not mix them.
- **LangChain is retrieval only.** There is exactly one orchestration layer and
  it is Strands. LangChain here only loads documents, splits them, embeds them,
  and answers "what are the most relevant fragments?". The Strands agent decides
  *when* to search (via the ``search_code`` tool); LangChain only decides *what*
  to return. The arrow points one way: the agent uses the retriever, never the
  reverse.
- The retriever lives behind the ``search_code`` tool interface so the backend
  (LangChain today, LlamaIndex tomorrow) can change without touching the agent.

The walking of the repo reuses the Ep.1 helpers (``iterSourceFiles``,
``readTextSafely``, ``EXTENSION_LANGUAGE_MAP``) rather than reimplementing it.
Symbol-based chunking reuses the tree-sitter parser loader from
``tools/structure.py``. All repo content is treated as untrusted: files are
only read, never executed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from code_copilot.config import (
    CHUNK_STRATEGY_FILE,
    CHUNK_STRATEGY_SYMBOL,
    RagConfig,
    loadRagConfig,
)
from code_copilot.utils import (
    EXTENSION_LANGUAGE_MAP,
    iterSourceFiles,
    readTextSafely,
)

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from langchain_core.documents import Document
    from langchain_core.vectorstores import VectorStoreRetriever

logger = logging.getLogger("code_copilot.knowledge")

# tree-sitter node types that define a top-level symbol worth isolating as a
# chunk. Mirrors the set used by tools/structure.py.
_DEFINITION_NODE_TYPES = {
    "function_definition",
    "class_definition",
    "function_declaration",
    "method_declaration",
    "class_declaration",
}


# --- Document loading -------------------------------------------------------


def _loadDocuments(repoPath: Path) -> list["Document"]:
    """Load every source file of the repo into a LangChain ``Document``.

    Each document carries the metadata required to cite the source later:
    ``path`` (relative to the repo root) and ``language``. Unreadable or binary
    files are skipped (``readTextSafely`` returns an empty string).
    """
    from langchain_core.documents import Document

    documents: list[Document] = []
    for filePath in iterSourceFiles(repoPath):
        languageName = EXTENSION_LANGUAGE_MAP.get(filePath.suffix)
        if languageName is None:
            continue
        sourceText = readTextSafely(filePath)
        if not sourceText:
            continue
        relativePath = str(filePath.relative_to(repoPath))
        documents.append(
            Document(
                page_content=sourceText,
                metadata={"path": relativePath, "language": languageName},
            )
        )
    return documents


# --- Chunking: by file ------------------------------------------------------


def _splitByFile(
    documents: list["Document"], *, chunkSize: int, chunkOverlap: int
) -> list["Document"]:
    """Split each document with ``RecursiveCharacterTextSplitter``.

    Robust default: preserves module context and never fails to produce chunks.
    Metadata (``path``, ``language``) is propagated to every chunk by the
    splitter.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunkSize, chunk_overlap=chunkOverlap
    )
    return splitter.split_documents(documents)


# --- Chunking: by symbol ----------------------------------------------------


def _extractSymbolSpans(sourceText: str, languageName: str) -> list[dict] | None:
    """Return top-level symbol spans using tree-sitter, or ``None``.

    Reuses the tree-sitter parser loader from ``tools/structure.py``. Each span
    is ``{"symbol": name, "start": byteStart, "end": byteEnd}`` in byte offsets
    into the UTF-8 encoded source. Returns ``None`` when tree-sitter or the
    grammar is unavailable (the caller then falls back to file chunking), and an
    empty list when parsing succeeded but found no top-level symbols.
    """
    from code_copilot.tools.structure import _loadTreeSitterParser

    parser = _loadTreeSitterParser(languageName)
    if parser is None:
        return None

    try:
        tree = parser.parse(sourceText.encode("utf-8"))
    except Exception:  # noqa: BLE001 - malformed source must not abort indexing
        return None

    spans: list[dict] = []

    def visit(node) -> None:
        if node.type in _DEFINITION_NODE_TYPES:
            nameNode = node.child_by_field_name("name")
            symbolName = (
                nameNode.text.decode("utf-8", errors="ignore")
                if nameNode is not None
                else node.type
            )
            spans.append(
                {"symbol": symbolName, "start": node.start_byte, "end": node.end_byte}
            )
            # Do not descend into a captured definition: keep chunks at the
            # top level (a class is one chunk, not one chunk per method).
            return
        for childNode in node.children:
            visit(childNode)

    visit(tree.root_node)
    return spans


def _symbolDocumentsForFile(document: "Document") -> list["Document"] | None:
    """Build one ``Document`` per top-level symbol in ``document``.

    Returns ``None`` to signal the caller to fall back to file chunking for this
    file (tree-sitter unavailable, or no parseable symbols) -- the same graceful
    degradation as Ep.1's structure analysis.
    """
    from langchain_core.documents import Document

    sourceText = document.page_content
    languageName = document.metadata.get("language", "")
    spans = _extractSymbolSpans(sourceText, languageName)
    if not spans:
        return None

    encoded = sourceText.encode("utf-8")
    symbolDocuments: list[Document] = []
    for span in spans:
        chunkText = encoded[span["start"] : span["end"]].decode("utf-8", errors="ignore")
        if not chunkText.strip():
            continue
        metadata = dict(document.metadata)
        metadata["symbol"] = span["symbol"]
        symbolDocuments.append(Document(page_content=chunkText, metadata=metadata))
    return symbolDocuments or None


def _splitBySymbol(
    documents: list["Document"], *, chunkSize: int, chunkOverlap: int
) -> list["Document"]:
    """Split documents by symbol, falling back to file chunking per file.

    For each file we try tree-sitter symbol extraction; if a file has no
    parseable symbols (or tree-sitter is unavailable) that single file is split
    with the file strategy instead. This keeps the whole codebase indexed even
    when parsing partially fails.
    """
    chunks: list["Document"] = []
    for document in documents:
        symbolDocuments = _symbolDocumentsForFile(document)
        if symbolDocuments is None:
            chunks.extend(
                _splitByFile(
                    [document], chunkSize=chunkSize, chunkOverlap=chunkOverlap
                )
            )
        else:
            chunks.extend(symbolDocuments)
    return chunks


def _chunkDocuments(
    documents: list["Document"], config: RagConfig
) -> list["Document"]:
    """Chunk documents according to the configured strategy."""
    if config.chunkStrategy == CHUNK_STRATEGY_SYMBOL:
        return _splitBySymbol(
            documents, chunkSize=config.chunkSize, chunkOverlap=config.chunkOverlap
        )
    # CHUNK_STRATEGY_FILE (default / fallback).
    return _splitByFile(
        documents, chunkSize=config.chunkSize, chunkOverlap=config.chunkOverlap
    )


# --- Embeddings + index -----------------------------------------------------


def _buildEmbeddings(config: RagConfig):
    """Create the Bedrock embeddings model.

    Stays on Bedrock (same provider as Ep.1) rather than adding a new one. The
    model id is configurable (``EMBEDDINGS_MODEL_ID``); the ``AWS_REGION`` /
    ``AWS_PROFILE`` environment already used by the chat model applies here too.
    """
    import os

    from langchain_aws import BedrockEmbeddings

    embeddingsArguments: dict = {"model_id": config.embeddingsModelId}
    regionName = os.environ.get("AWS_REGION")
    if regionName:
        embeddingsArguments["region_name"] = regionName
    profileName = os.environ.get("AWS_PROFILE")
    if profileName:
        embeddingsArguments["credentials_profile_name"] = profileName
    return BedrockEmbeddings(**embeddingsArguments)


def _resolveRepoPath(repoPath: str | Path) -> Path:
    """Resolve a repo source (local path OR GitHub URL) to a local directory.

    Reuses the Ep.1 ``resolveRepository`` helper so a URL is shallow-cloned
    (``git clone --depth 1``, no hooks) exactly like ``ingest_repository`` does.
    A local path is validated and returned as-is.
    """
    from code_copilot.tools.ingest import resolveRepository

    resolved = resolveRepository(str(repoPath))
    return resolved.localPath


def _analyzeChunks(chunks: list["Document"]) -> dict:
    """Summarize the chunk set: counts, how many carry a symbol, top files."""
    withSymbol = sum(1 for chunk in chunks if chunk.metadata.get("symbol"))
    fileCounts: dict[str, int] = {}
    for chunk in chunks:
        path = chunk.metadata.get("path", "?")
        fileCounts[path] = fileCounts.get(path, 0) + 1
    topFiles = sorted(fileCounts.items(), key=lambda item: item[1], reverse=True)[:10]
    return {
        "chunk_count": len(chunks),
        "chunks_with_symbol": withSymbol,
        "top_chunked_files": [{"path": path, "chunks": n} for path, n in topFiles],
    }


def analyzeRepository(documents: list["Document"], basePath: Path) -> dict:
    """Build a lightweight, deterministic analysis of the indexed repo.

    Reuses the Ep.1 detectors (languages, size, directory tree) over the files
    that were actually loaded for indexing, so the endpoint can report *what*
    was analyzed without an LLM or extra scans.
    """
    from code_copilot.utils import buildDirectoryTree, detectPackageManagers

    languageCounts: dict[str, int] = {}
    totalBytes = 0
    for document in documents:
        language = document.metadata.get("language", "Unknown")
        languageCounts[language] = languageCounts.get(language, 0) + 1
        totalBytes += len(document.page_content.encode("utf-8"))

    sortedLanguages = dict(
        sorted(languageCounts.items(), key=lambda item: item[1], reverse=True)
    )
    return {
        "file_count": len(documents),
        "total_size_bytes": totalBytes,
        "languages": sortedLanguages,
        "package_managers": detectPackageManagers(basePath),
        "directory_tree": buildDirectoryTree(basePath),
    }


def buildIndex(repoPath: str | Path, config: RagConfig | None = None):
    """Ingest ``repoPath`` and return a local FAISS index over its code.

    Thin wrapper over :func:`buildIndexWithAnalysis` that returns only the index
    (kept for backward compatibility).
    """
    index, _analysis = buildIndexWithAnalysis(repoPath, config)
    return index


def buildIndexWithAnalysis(repoPath: str | Path, config: RagConfig | None = None):
    """Ingest ``repoPath`` and return ``(index, analysis)``.

    ``repoPath`` may be a local directory OR a GitHub URL (URLs are shallow
    cloned via the Ep.1 resolver). Steps: load every source file into a
    ``Document`` (with ``path`` / ``language`` metadata) -> chunk (by file or by
    symbol) -> embed with Bedrock -> build a FAISS index. ``analysis`` describes
    *what* was analyzed (source, files, languages, size, chunking) so callers
    such as the ``POST /index`` endpoint can surface it.

    Args:
        repoPath: Local path or GitHub URL of the repository to index.
        config: RAG configuration; loaded from the environment when omitted.

    Returns:
        A tuple ``(FAISS index, analysis dict)``.

    Raises:
        ValueError: When the repo has no indexable source files.
    """
    from langchain_community.vectorstores import FAISS

    ragConfig = config or loadRagConfig()
    basePath = _resolveRepoPath(repoPath)
    documents = _loadDocuments(basePath)
    if not documents:
        raise ValueError(f"No indexable source files found under {basePath}")

    chunks = _chunkDocuments(documents, ragConfig)
    logger.info(
        "knowledge.buildIndex repo=%s files=%d chunks=%d strategy=%s",
        basePath,
        len(documents),
        len(chunks),
        ragConfig.chunkStrategy,
    )
    embeddings = _buildEmbeddings(ragConfig)
    index = FAISS.from_documents(chunks, embeddings)

    analysis = {
        "source": str(repoPath),
        "resolved_path": str(basePath),
        "chunk_strategy": ragConfig.chunkStrategy,
        "chunk_size": ragConfig.chunkSize,
        "chunk_overlap": ragConfig.chunkOverlap,
        "embeddings_model": ragConfig.embeddingsModelId,
        "index_dir": str(Path(ragConfig.indexDir).expanduser()),
        **analyzeRepository(documents, basePath),
        **_analyzeChunks(chunks),
    }
    return index, analysis


def saveIndex(index, config: RagConfig | None = None) -> str:
    """Persist a FAISS ``index`` to disk and return the directory used."""
    ragConfig = config or loadRagConfig()
    indexDir = Path(ragConfig.indexDir).expanduser()
    indexDir.mkdir(parents=True, exist_ok=True)
    index.save_local(str(indexDir))
    logger.info("knowledge.saveIndex dir=%s", indexDir)
    return str(indexDir)


def loadIndex(config: RagConfig | None = None):
    """Load a persisted FAISS index from disk, or ``None`` when absent.

    The pickle deserialization is enabled because the index file is produced by
    this tool locally (not fetched from an untrusted source).
    """
    from langchain_community.vectorstores import FAISS

    ragConfig = config or loadRagConfig()
    indexDir = Path(ragConfig.indexDir).expanduser()
    if not (indexDir / "index.faiss").exists():
        return None
    embeddings = _buildEmbeddings(ragConfig)
    return FAISS.load_local(
        str(indexDir), embeddings, allow_dangerous_deserialization=True
    )


def buildRetriever(
    repoPath: str | Path,
    *,
    config: RagConfig | None = None,
    persist: bool = True,
    forceRebuild: bool = False,
    k: int = 5,
) -> "VectorStoreRetriever":
    """Build (or load) the index and expose it as a retriever.

    With ``forceRebuild=False`` a persisted index at the configured path is
    reused; otherwise the repo is (re)indexed and optionally saved. Use
    ``forceRebuild=True`` to index a specific new repository even when an old
    index exists on disk. The returned object is a plain LangChain retriever:
    the agent only ever sees it through the ``search_code`` tool.

    Args:
        repoPath: Local path or GitHub URL of the repository to index.
        config: RAG configuration; loaded from the environment when omitted.
        persist: Save the freshly built index to disk when ``True``.
        forceRebuild: Reindex ``repoPath`` even if a persisted index exists.
        k: Default number of documents the retriever returns.

    Returns:
        A ``VectorStoreRetriever`` over the code index.
    """
    ragConfig = config or loadRagConfig()
    index = None if forceRebuild else loadIndex(ragConfig)
    if index is None:
        index = buildIndex(repoPath, ragConfig)
        if persist:
            saveIndex(index, ragConfig)
    return index.as_retriever(search_kwargs={"k": k})


def buildRetrieverWithAnalysis(
    repoPath: str | Path,
    *,
    config: RagConfig | None = None,
    persist: bool = True,
    k: int = 5,
):
    """Index ``repoPath`` and return ``(retriever, analysis)``.

    Always (re)indexes the given repo -- this is the ``POST /index`` path -- so
    the returned analysis describes exactly what was just processed, and the
    fresh index replaces any stale one on disk.
    """
    ragConfig = config or loadRagConfig()
    index, analysis = buildIndexWithAnalysis(repoPath, ragConfig)
    if persist:
        saveIndex(index, ragConfig)
    return index.as_retriever(search_kwargs={"k": k}), analysis
