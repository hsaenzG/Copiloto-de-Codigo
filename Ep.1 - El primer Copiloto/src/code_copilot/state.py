"""In-process analysis state store.

Tools that produce large structured outputs (ingest, structure, the scanners,
quality) write their results here keyed by repository path. Downstream tools
(explain, recommend, report) read those results back instead of receiving the
full dictionaries through the model's tool-use channel. This keeps the
arguments the model must emit small (just a repo_path string), which avoids
malformed tool-use sequences on large payloads.

The store is a simple module-level dict. It is not persisted and is scoped to a
single process, which matches one CLI run.
"""

from __future__ import annotations

from pathlib import Path

# Maps an absolute repo path -> {result_key: result_dict/str}.
_ANALYSIS_STATE: dict[str, dict] = {}


def _normalizeKey(repoPath: str) -> str:
    """Normalize a repo path so writes and reads use the same key."""
    return str(Path(repoPath).expanduser().resolve())


def storeResult(repoPath: str, resultKey: str, resultValue) -> None:
    """Store ``resultValue`` under ``resultKey`` for the given repository."""
    normalizedKey = _normalizeKey(repoPath)
    _ANALYSIS_STATE.setdefault(normalizedKey, {})[resultKey] = resultValue


def getResult(repoPath: str, resultKey: str, default=None):
    """Return a previously stored result, or ``default`` when absent."""
    normalizedKey = _normalizeKey(repoPath)
    return _ANALYSIS_STATE.get(normalizedKey, {}).get(resultKey, default)


def getAllResults(repoPath: str) -> dict:
    """Return all stored results for a repository as a dict."""
    return dict(_ANALYSIS_STATE.get(_normalizeKey(repoPath), {}))


def clearResults(repoPath: str) -> None:
    """Remove all stored results for a repository (used by tests)."""
    _ANALYSIS_STATE.pop(_normalizeKey(repoPath), None)
