"""Unit tests for shared utilities."""

from __future__ import annotations

from pathlib import Path

from code_copilot.utils import (
    detectLanguages,
    detectPackageManagers,
    isGithubUrl,
    iterSourceFiles,
    redactSecret,
)


def test_isGithubUrl_acceptsHttpsAndSsh():
    assert isGithubUrl("https://github.com/octocat/Hello-World")
    assert isGithubUrl("https://github.com/octocat/Hello-World.git")
    assert isGithubUrl("git@github.com:octocat/Hello-World.git")


def test_isGithubUrl_rejectsNonGithub():
    assert not isGithubUrl("/local/path/repo")
    assert not isGithubUrl("https://gitlab.com/foo/bar")


def test_iterSourceFiles_skipsIgnoredDirectories(sampleRepo: Path):
    files = iterSourceFiles(sampleRepo)
    fileNames = {path.name for path in files}
    assert "main.py" in fileNames
    assert "index.js" in fileNames
    # node_modules content must be excluded.
    assert "junk.js" not in fileNames


def test_detectLanguages_countsByLanguage(sampleRepo: Path):
    languages = detectLanguages(iterSourceFiles(sampleRepo))
    assert languages.get("Python", 0) >= 2
    assert languages.get("JavaScript", 0) >= 1


def test_detectPackageManagers(sampleRepo: Path):
    managers = detectPackageManagers(sampleRepo)
    assert managers.get("requirements.txt") == "pip"
    assert managers.get("package.json") == "npm"


def test_redactSecret_neverReturnsFullValue():
    redacted = redactSecret("AKIAIOSFODNN7EXAMPLE")
    assert redacted.startswith("AKIA")
    assert "IOSFODNN7EXAMPLE" not in redacted
    assert "*" in redacted


def test_redactSecret_handlesShortAndEmpty():
    assert redactSecret("") == "<empty>"
    assert set(redactSecret("abc")) == {"*"}
