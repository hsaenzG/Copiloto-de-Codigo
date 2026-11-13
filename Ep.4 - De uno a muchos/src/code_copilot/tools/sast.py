"""Tool: scan_sast.

Runs static application security testing with Semgrep when the CLI is
installed. If Semgrep is unavailable, the tool reports the limitation instead
of failing, and applies a small set of built-in regex heuristics as a partial
fallback so the pipeline still yields signal.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from strands import tool

from code_copilot.state import storeResult
from code_copilot.utils import EXTENSION_LANGUAGE_MAP, iterSourceFiles, readTextSafely

# Minimal built-in heuristics used only when Semgrep is not installed.
# Each entry: (ruleId, severity, description, compiled regex).
_FALLBACK_RULES = [
    (
        "python.eval-use",
        "HIGH",
        "Use of eval() can lead to code injection.",
        re.compile(r"\beval\s*\("),
    ),
    (
        "python.exec-use",
        "HIGH",
        "Use of exec() can lead to code injection.",
        re.compile(r"\bexec\s*\("),
    ),
    (
        "shell.subprocess-shell-true",
        "HIGH",
        "subprocess call with shell=True can enable command injection.",
        re.compile(r"shell\s*=\s*True"),
    ),
    (
        "js.child-process-exec",
        "MEDIUM",
        "child_process.exec with untrusted input can enable command injection.",
        re.compile(r"child_process\.exec\s*\("),
    ),
    (
        "generic.insecure-yaml-load",
        "MEDIUM",
        "yaml.load without SafeLoader can execute arbitrary objects.",
        re.compile(r"yaml\.load\s*\((?!.*Loader\s*=\s*yaml\.SafeLoader)"),
    ),
]


def _findSemgrep() -> str | None:
    """Locate the semgrep executable.

    Checks the directory of the running Python interpreter first (so a semgrep
    installed in the active virtualenv is found even when its bin dir is not on
    PATH), then falls back to a normal PATH lookup. Returns the path, or None
    when semgrep is not installed.
    """
    interpreterBinDir = Path(sys.executable).parent
    candidate = interpreterBinDir / "semgrep"
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which("semgrep")


def _runSemgrep(basePath: Path) -> list[dict] | None:
    """Run Semgrep CLI and normalize its findings.

    Returns None when Semgrep is not installed. Raises no exception on a
    non-zero exit that still produced JSON (Semgrep exits non-zero when it
    finds issues).
    """
    semgrepPath = _findSemgrep()
    if semgrepPath is None:
        return None

    completed = subprocess.run(
        [semgrepPath, "--config", "auto", "--json", "--quiet", str(basePath)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    # Semgrep returns exit code 1 when findings exist; both 0 and 1 are valid.
    if completed.returncode not in (0, 1) or not completed.stdout.strip():
        raise RuntimeError(completed.stderr.strip() or "semgrep produced no output")

    parsed = json.loads(completed.stdout)
    findings: list[dict] = []
    for result in parsed.get("results", []):
        extra = result.get("extra", {})
        findings.append(
            {
                "file": result.get("path", ""),
                "line": result.get("start", {}).get("line", 0),
                "severity": extra.get("severity", "INFO"),
                "rule": result.get("check_id", ""),
                "description": extra.get("message", ""),
                "suggested_fix": extra.get("fix", ""),
            }
        )
    return findings


def _runFallbackHeuristics(basePath: Path) -> list[dict]:
    """Apply built-in regex rules when Semgrep is unavailable."""
    findings: list[dict] = []
    for filePath in iterSourceFiles(basePath):
        if filePath.suffix not in EXTENSION_LANGUAGE_MAP:
            continue
        sourceText = readTextSafely(filePath)
        if not sourceText:
            continue
        for lineNumber, lineText in enumerate(sourceText.splitlines(), start=1):
            for ruleId, severity, description, pattern in _FALLBACK_RULES:
                if pattern.search(lineText):
                    findings.append(
                        {
                            "file": str(filePath.relative_to(basePath)),
                            "line": lineNumber,
                            "severity": severity,
                            "rule": ruleId,
                            "description": description,
                            "suggested_fix": "",
                        }
                    )
    return findings


@tool
def scan_sast(repo_path: str) -> dict:
    """Run static security analysis (SAST) over the repository.

    Prefers the Semgrep CLI (``semgrep --config auto``). If Semgrep is not
    installed, applies a small set of built-in heuristics and notes the
    degraded mode. Never executes repository code.

    Args:
        repo_path: Path to the ingested repository on disk.

    Returns:
        A dict with keys: engine, findings (list of {file, line, severity,
        rule, description, suggested_fix}), finding_count, and optionally
        limitation when running in degraded mode.
    """
    basePath = Path(repo_path).expanduser().resolve()
    if not basePath.is_dir():
        return {"error": f"repo_path is not a directory: {basePath}"}

    try:
        semgrepFindings = _runSemgrep(basePath)
    except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as sastError:
        result = {
            "engine": "semgrep",
            "findings": [],
            "finding_count": 0,
            "limitation": f"Semgrep run failed: {sastError}",
        }
    else:
        if semgrepFindings is not None:
            result = {
                "engine": "semgrep",
                "findings": semgrepFindings,
                "finding_count": len(semgrepFindings),
            }
        else:
            fallbackFindings = _runFallbackHeuristics(basePath)
            result = {
                "engine": "builtin-heuristics",
                "findings": fallbackFindings,
                "finding_count": len(fallbackFindings),
                "limitation": (
                    "Semgrep CLI not found; used limited built-in heuristics. "
                    "Install semgrep for full SAST coverage."
                ),
            }

    storeResult(str(basePath), "sast", result)
    return result
