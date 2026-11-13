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

import json
import logging
from pathlib import Path

logger = logging.getLogger("code_copilot.state")

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


def snapshot() -> dict[str, dict]:
    """Return a deep-ish copy of the whole in-process analysis state.

    Episode 2: the shared state must survive between runs alongside the
    conversation. This exposes the module-level store so a session layer can
    persist it, without duplicating the storage logic that lives here.
    """
    return {repoKey: dict(results) for repoKey, results in _ANALYSIS_STATE.items()}


def restore(stateSnapshot: dict[str, dict]) -> None:
    """Replace the in-process analysis state with a previously saved snapshot."""
    _ANALYSIS_STATE.clear()
    for repoKey, results in (stateSnapshot or {}).items():
        _ANALYSIS_STATE[repoKey] = dict(results)


def saveTo(statePath: Path) -> None:
    """Persist the current analysis state to a JSON file (best-effort)."""
    try:
        statePath.parent.mkdir(parents=True, exist_ok=True)
        statePath.write_text(
            json.dumps(snapshot(), indent=2, default=str), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError) as saveError:
        logger.warning("state.saveTo failed for %s: %s", statePath, saveError)


def loadFrom(statePath: Path) -> None:
    """Restore analysis state from a JSON file if it exists (best-effort)."""
    if not statePath.exists():
        return
    try:
        restore(json.loads(statePath.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as loadError:
        logger.warning("state.loadFrom failed for %s: %s", statePath, loadError)
