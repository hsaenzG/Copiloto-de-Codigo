"""Retrieval (recall@k) and chunking tests for the code RAG layer.

Deterministic and offline: uses a hashing embedding double so FAISS works
without Bedrock or the network. Verifies that:
- documents are loaded with path/language metadata,
- both chunking strategies produce chunks (symbol falls back to file when a
  file has no parseable symbols),
- a known answer is retrieved in the top-k for a targeted query,
- symbol chunking attaches the ``symbol`` metadata used to cite the source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_copilot.config import RagConfig
from code_copilot.knowledge import (
    _chunkDocuments,
    _loadDocuments,
    _splitByFile,
    _splitBySymbol,
)
from fakes import HashingEmbeddings


def _fileConfig() -> RagConfig:
    return RagConfig(
        chunkStrategy="file",
        chunkSize=400,
        chunkOverlap=50,
        indexDir="/tmp/unused",
        embeddingsModelId="fake",
    )


def _symbolConfig() -> RagConfig:
    return RagConfig(
        chunkStrategy="symbol",
        chunkSize=400,
        chunkOverlap=50,
        indexDir="/tmp/unused",
        embeddingsModelId="fake",
    )


@pytest.fixture
def codeRepo(tmp_path: Path) -> Path:
    """A small repo with a distinct auth symbol to retrieve."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        "def authenticate(token):\n"
        "    # verify the session token and authorize the user\n"
        "    return validate_credentials(token)\n"
        "\n"
        "class SessionGuard:\n"
        "    def check_permission(self, user):\n"
        "        return user.is_authenticated\n",
        encoding="utf-8",
    )
    (repo / "report.py").write_text(
        "def build_report(data):\n"
        "    # assemble the final markdown report\n"
        "    return render_markdown(data)\n",
        encoding="utf-8",
    )
    (repo / "notes.txt").write_text("not a source file", encoding="utf-8")
    return repo


def _buildFaiss(documents, config):
    from langchain_community.vectorstores import FAISS

    chunks = _chunkDocuments(documents, config)
    return FAISS.from_documents(chunks, HashingEmbeddings()), chunks


def test_resolve_repo_path_accepts_local_dir(codeRepo: Path):
    """A local directory resolves to itself (no clone, offline)."""
    from code_copilot.knowledge import _resolveRepoPath

    resolved = _resolveRepoPath(str(codeRepo))
    assert resolved == codeRepo.resolve()


def test_load_documents_sets_metadata(codeRepo: Path):
    documents = _loadDocuments(codeRepo)
    paths = {doc.metadata["path"] for doc in documents}
    # Only recognized source files; notes.txt is excluded.
    assert paths == {"auth.py", "report.py"}
    assert all(doc.metadata.get("language") == "Python" for doc in documents)


def test_symbol_chunking_attaches_symbol_metadata(codeRepo: Path):
    documents = _loadDocuments(codeRepo)
    symbolChunks = _splitBySymbol(documents, chunkSize=400, chunkOverlap=50)
    symbols = {chunk.metadata.get("symbol") for chunk in symbolChunks}
    assert "authenticate" in symbols
    assert "SessionGuard" in symbols


def test_file_chunking_has_no_symbol_metadata(codeRepo: Path):
    documents = _loadDocuments(codeRepo)
    fileChunks = _splitByFile(documents, chunkSize=400, chunkOverlap=50)
    assert fileChunks  # produced chunks
    assert all("symbol" not in chunk.metadata for chunk in fileChunks)


def test_symbol_chunking_falls_back_for_unparseable(tmp_path: Path):
    """A file with no parseable symbols still gets indexed (file fallback)."""
    from langchain_core.documents import Document

    document = Document(
        page_content="x = 1\ny = 2\nz = x + y\n",
        metadata={"path": "flat.py", "language": "Python"},
    )
    chunks = _splitBySymbol([document], chunkSize=400, chunkOverlap=50)
    assert chunks  # did not silently drop the file
    assert all("symbol" not in chunk.metadata for chunk in chunks)


def test_analyze_repository_reports_languages_and_size(codeRepo: Path):
    from code_copilot.knowledge import analyzeRepository

    documents = _loadDocuments(codeRepo)
    analysis = analyzeRepository(documents, codeRepo)
    assert analysis["file_count"] == 2
    assert analysis["languages"].get("Python") == 2
    assert analysis["total_size_bytes"] > 0
    assert "directory_tree" in analysis


def test_analyze_chunks_counts_symbols(codeRepo: Path):
    from code_copilot.knowledge import _analyzeChunks

    documents = _loadDocuments(codeRepo)
    symbolChunks = _splitBySymbol(documents, chunkSize=400, chunkOverlap=50)
    summary = _analyzeChunks(symbolChunks)
    assert summary["chunk_count"] == len(symbolChunks)
    assert summary["chunks_with_symbol"] >= 1
    assert isinstance(summary["top_chunked_files"], list)


@pytest.mark.parametrize("makeConfig", [_fileConfig, _symbolConfig])
def test_recall_at_k_finds_known_answer(codeRepo: Path, makeConfig):
    """A targeted query retrieves the auth file in the top-k, both strategies."""
    documents = _loadDocuments(codeRepo)
    index, _ = _buildFaiss(documents, makeConfig())
    retriever = index.as_retriever(search_kwargs={"k": 3})

    results = retriever.invoke("where is authentication and the session token handled")
    retrievedPaths = [doc.metadata["path"] for doc in results]
    assert "auth.py" in retrievedPaths


def test_chunking_comparison_recall(codeRepo: Path):
    """Compare recall between file and symbol chunking on the same questions.

    Both strategies must retrieve the correct file for each question; symbol
    chunking additionally pinpoints the symbol.
    """
    questions = {
        "how are users authenticated with a token": "auth.py",
        "where is the markdown report assembled": "report.py",
    }
    documents = _loadDocuments(codeRepo)

    for config in (_fileConfig(), _symbolConfig()):
        index, _ = _buildFaiss(documents, config)
        retriever = index.as_retriever(search_kwargs={"k": 2})
        for question, expectedPath in questions.items():
            paths = [doc.metadata["path"] for doc in retriever.invoke(question)]
            assert expectedPath in paths, (config.chunkStrategy, question, paths)
