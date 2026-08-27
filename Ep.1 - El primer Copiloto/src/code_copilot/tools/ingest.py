"""Tool: ingest_repository.

Clones a remote GitHub repository (or validates a local path) and returns
metadata: detected languages, file counts, size, package managers, and
relevant config files.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from strands import tool

from code_copilot.state import storeResult
from code_copilot.utils import (
    RepositorySource,
    buildDirectoryTree,
    detectConfigFiles,
    detectLanguages,
    detectPackageManagers,
    isGithubUrl,
    iterSourceFiles,
)

# Root directory under which remote repositories are cloned.
CLONE_ROOT = Path(".copilot_workdir")


def resolveRepository(source: str) -> RepositorySource:
    """Resolve a repo ``source`` (URL or local path) to a local directory.

    Remote URLs are cloned shallowly into a temp dir under ``CLONE_ROOT``.
    Local paths are validated to exist. The repo content is never executed.
    """
    trimmedSource = source.strip()

    if isGithubUrl(trimmedSource):
        CLONE_ROOT.mkdir(parents=True, exist_ok=True)
        cloneTarget = Path(tempfile.mkdtemp(prefix="repo_", dir=str(CLONE_ROOT)))
        # Shallow clone; --depth 1 avoids pulling full history. No hooks run.
        completed = subprocess.run(
            ["git", "clone", "--depth", "1", trimmedSource, str(cloneTarget)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if completed.returncode != 0:
            shutil.rmtree(cloneTarget, ignore_errors=True)
            raise RuntimeError(f"git clone failed: {completed.stderr.strip()}")
        return RepositorySource(
            localPath=cloneTarget, isRemote=True, originUrl=trimmedSource
        )

    localPath = Path(trimmedSource).expanduser().resolve()
    if not localPath.exists() or not localPath.is_dir():
        raise FileNotFoundError(f"Local repository path not found: {localPath}")
    return RepositorySource(localPath=localPath, isRemote=False)


@tool
def ingest_repository(source: str) -> dict:
    """Ingest a repository from a GitHub URL or a local path.

    Clones the repository if a URL is given, otherwise validates the local
    path, then returns an inventory of the codebase. Directories such as
    node_modules, vendor, build and dist are ignored.

    Args:
        source: A GitHub repository URL (https or ssh) or a local directory path.

    Returns:
        A dict with keys: repo_path, is_remote, origin_url, file_count,
        total_size_bytes, languages, package_managers, config_files, and
        directory_tree.
    """
    try:
        repository = resolveRepository(source)
    except (RuntimeError, FileNotFoundError) as ingestError:
        return {"error": str(ingestError), "source": source}

    repoPath = repository.localPath
    sourceFiles = iterSourceFiles(repoPath)
    totalSizeBytes = 0
    for filePath in sourceFiles:
        try:
            totalSizeBytes += filePath.stat().st_size
        except OSError:
            continue

    ingestSummary = {
        "repo_path": str(repoPath),
        "is_remote": repository.isRemote,
        "origin_url": repository.originUrl,
        "file_count": len(sourceFiles),
        "total_size_bytes": totalSizeBytes,
        "languages": detectLanguages(sourceFiles),
        "package_managers": detectPackageManagers(repoPath),
        "config_files": detectConfigFiles(repoPath),
        "directory_tree": buildDirectoryTree(repoPath),
    }
    storeResult(str(repoPath), "ingest", ingestSummary)
    return ingestSummary
