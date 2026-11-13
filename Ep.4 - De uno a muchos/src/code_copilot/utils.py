"""Shared utilities used across tools.

These helpers are pure and free of network/LLM side effects so they can be unit
tested in isolation. All repository content is treated as untrusted: files are
only read, never executed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Directories that are never worth analyzing and are excluded from every walk.
IGNORED_DIRECTORIES = {
    ".git",
    "node_modules",
    "vendor",
    "build",
    "dist",
    "target",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    "coverage",
    ".next",
    ".gradle",
    # Build artifacts and this tool's own working/report dirs.
    "cdk.out",
    ".aws-sam",
    ".copilot_workdir",
    "reports",
}

# Map of file extension -> language name for lightweight language detection.
EXTENSION_LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".java": "Java",
    ".rb": "Ruby",
    ".php": "PHP",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cs": "C#",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
}

# Manifest file name -> package manager, used for dependency detection.
MANIFEST_PACKAGE_MANAGERS = {
    "package.json": "npm",
    "requirements.txt": "pip",
    "pyproject.toml": "pip",
    "Pipfile": "pipenv",
    "go.mod": "go",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "Gemfile": "bundler",
    "composer.json": "composer",
    "Cargo.toml": "cargo",
}

# Config files worth surfacing in the inventory.
RELEVANT_CONFIG_FILES = {
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env.example",
    ".gitlab-ci.yml",
    "Makefile",
}


@dataclass
class RepositorySource:
    """Resolved repository source.

    Attributes:
        localPath: Absolute path to the repository on disk.
        isRemote: True when the source was a URL that had to be cloned.
        originUrl: The original GitHub URL, when applicable.
    """

    localPath: Path
    isRemote: bool = False
    originUrl: str | None = None


_GITHUB_URL_PATTERN = re.compile(
    r"^(https://github\.com/|git@github\.com:)[\w.\-]+/[\w.\-]+(\.git)?/?$"
)


def isGithubUrl(source: str) -> bool:
    """Return True when ``source`` looks like a GitHub repository URL."""
    return bool(_GITHUB_URL_PATTERN.match(source.strip()))


def iterSourceFiles(repoPath: Path) -> list[Path]:
    """Walk ``repoPath`` and return analyzable source files.

    Ignored directories (node_modules, vendor, build, ...) are skipped. Only
    files with a recognized source extension are returned.
    """
    collectedFiles: list[Path] = []
    for currentRoot, directoryNames, fileNames in os.walk(repoPath):
        # Prune ignored directories in place so os.walk does not descend.
        directoryNames[:] = [
            directoryName
            for directoryName in directoryNames
            if directoryName not in IGNORED_DIRECTORIES
        ]
        for fileName in fileNames:
            filePath = Path(currentRoot) / fileName
            if filePath.suffix in EXTENSION_LANGUAGE_MAP:
                collectedFiles.append(filePath)
    return collectedFiles


def detectLanguages(sourceFiles: list[Path]) -> dict[str, int]:
    """Return a map of language name -> file count for the given files."""
    languageCounts: dict[str, int] = {}
    for filePath in sourceFiles:
        languageName = EXTENSION_LANGUAGE_MAP.get(filePath.suffix)
        if languageName:
            languageCounts[languageName] = languageCounts.get(languageName, 0) + 1
    return dict(sorted(languageCounts.items(), key=lambda item: item[1], reverse=True))


def detectPackageManagers(repoPath: Path) -> dict[str, str]:
    """Return a map of manifest file name -> package manager found in the repo."""
    detected: dict[str, str] = {}
    for manifestName, packageManager in MANIFEST_PACKAGE_MANAGERS.items():
        if (repoPath / manifestName).exists():
            detected[manifestName] = packageManager
    return detected


def detectConfigFiles(repoPath: Path) -> list[str]:
    """Return the list of relevant config files present at the repo root."""
    return [name for name in sorted(RELEVANT_CONFIG_FILES) if (repoPath / name).exists()]


def buildDirectoryTree(repoPath: Path, maxEntries: int = 200) -> str:
    """Build a compact, human-readable directory tree summary.

    The tree is truncated to ``maxEntries`` lines to keep the output bounded.
    """
    treeLines: list[str] = []
    entryCount = 0
    for currentRoot, directoryNames, fileNames in os.walk(repoPath):
        directoryNames[:] = [
            directoryName
            for directoryName in directoryNames
            if directoryName not in IGNORED_DIRECTORIES
        ]
        directoryNames.sort()
        relativeRoot = Path(currentRoot).relative_to(repoPath)
        depth = 0 if str(relativeRoot) == "." else len(relativeRoot.parts)
        indent = "  " * depth
        if str(relativeRoot) != ".":
            treeLines.append(f"{indent}{relativeRoot.name}/")
            entryCount += 1
        for fileName in sorted(fileNames):
            if entryCount >= maxEntries:
                treeLines.append(f"{indent}  ... (truncated)")
                return "\n".join(treeLines)
            treeLines.append(f"{indent}  {fileName}")
            entryCount += 1
    return "\n".join(treeLines)


# --- Secret redaction -------------------------------------------------------

def redactSecret(secretValue: str, visibleChars: int = 4) -> str:
    """Redact a secret, keeping only the first few characters visible.

    Never returns the full secret. Used so that scan output can reference a
    finding without leaking the credential itself.
    """
    if not secretValue:
        return "<empty>"
    trimmed = secretValue.strip()
    if len(trimmed) <= visibleChars:
        return "*" * len(trimmed)
    return f"{trimmed[:visibleChars]}{'*' * (len(trimmed) - visibleChars)}"


def readTextSafely(filePath: Path, maxBytes: int = 2_000_000) -> str:
    """Read a text file defensively.

    Binary or oversized files return an empty string rather than raising, so a
    single unreadable file never aborts a scan.
    """
    try:
        if filePath.stat().st_size > maxBytes:
            return ""
        return filePath.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        return ""
