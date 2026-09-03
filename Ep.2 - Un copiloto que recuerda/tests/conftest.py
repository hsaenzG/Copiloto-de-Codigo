"""Shared pytest fixtures.

Provides a small, self-contained sample repository on disk so tools can be
tested without network access or a real clone.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _callTool(toolObject, **keywordArguments):
    """Invoke a Strands @tool's underlying function directly."""
    underlyingFunction = (
        getattr(toolObject, "original", None)
        or getattr(toolObject, "__wrapped__", None)
        or toolObject
    )
    return underlyingFunction(**keywordArguments)


@pytest.fixture
def callTool():
    """Expose the tool-invocation helper to tests."""
    return _callTool


@pytest.fixture
def sampleRepo(tmp_path: Path) -> Path:
    """Create a minimal multi-file sample repository and return its path."""
    repoRoot = tmp_path / "sample_repo"
    repoRoot.mkdir()

    # A Python entry point with a deliberate SAST issue (eval) and a secret.
    (repoRoot / "main.py").write_text(
        "import os\n"
        "\n"
        "API_KEY = \"AKIAIOSFODNN7EXAMPLE\"\n"
        "\n"
        "def run(userInput):\n"
        "    # insecure on purpose for testing\n"
        "    return eval(userInput)\n"
        "\n"
        "class Service:\n"
        "    def handle(self):\n"
        "        return run('1 + 1')\n",
        encoding="utf-8",
    )

    # A JS module with an import.
    (repoRoot / "index.js").write_text(
        "import express from 'express';\n"
        "const app = express();\n"
        "function start() { app.listen(3000); }\n"
        "start();\n",
        encoding="utf-8",
    )

    # A pinned Python manifest for SCA parsing.
    (repoRoot / "requirements.txt").write_text(
        "requests==2.31.0\n# a comment\nflask==2.0.1\n",
        encoding="utf-8",
    )

    # A package.json for SCA parsing.
    (repoRoot / "package.json").write_text(
        '{\n  "name": "sample",\n  "dependencies": {"express": "^4.18.2"}\n}\n',
        encoding="utf-8",
    )

    # Ignored directory that must be skipped by the walker.
    ignoredDir = repoRoot / "node_modules" / "pkg"
    ignoredDir.mkdir(parents=True)
    (ignoredDir / "junk.js").write_text("console.log('ignore me');\n", encoding="utf-8")

    # A test file so assess_quality detects tests.
    (repoRoot / "test_main.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    return repoRoot
