"""Tool: assess_quality.

Computes lightweight, deterministic maintainability heuristics: large files,
long functions, approximate cyclomatic complexity, comment density, and test
presence. Produces a 0-100 health score plus a list of concrete issues.
"""

from __future__ import annotations

import re
from pathlib import Path

from strands import tool

from code_copilot.state import storeResult
from code_copilot.utils import (
    EXTENSION_LANGUAGE_MAP,
    iterSourceFiles,
    readTextSafely,
)

# Thresholds for flagging maintainability issues.
LARGE_FILE_LINE_THRESHOLD = 400
LONG_FUNCTION_LINE_THRESHOLD = 60
HIGH_COMPLEXITY_THRESHOLD = 15

# Tokens that add a branch to approximate cyclomatic complexity.
_BRANCH_TOKENS = re.compile(
    r"\b(if|for|while|case|catch|elif|and|or|&&|\|\|)\b|\?"
)

# Heuristic to detect that a repo has any tests.
_TEST_PATH_HINTS = ("test", "spec", "__tests__")


def _approximateComplexity(sourceText: str) -> int:
    """Approximate cyclomatic complexity by counting branch tokens (+1 base)."""
    return len(_BRANCH_TOKENS.findall(sourceText)) + 1


def _hasTests(sourceFiles: list[Path]) -> bool:
    """Return True when any file path suggests the presence of tests."""
    for filePath in sourceFiles:
        loweredPath = str(filePath).lower()
        if any(hint in loweredPath for hint in _TEST_PATH_HINTS):
            return True
    return False


@tool
def assess_quality(repo_path: str) -> dict:
    """Assess code quality and maintainability with deterministic heuristics.

    Flags large files, long functions, high-complexity files, low comment
    density, and absence of tests. Returns a 0-100 health score where higher
    is healthier.

    Args:
        repo_path: Path to the ingested repository on disk.

    Returns:
        A dict with keys: health_score, has_tests, files_measured, issues
        (list of {file, kind, detail}), and metrics summary.
    """
    basePath = Path(repo_path).expanduser().resolve()
    if not basePath.is_dir():
        return {"error": f"repo_path is not a directory: {basePath}"}

    sourceFiles = iterSourceFiles(basePath)
    issues: list[dict] = []
    totalLines = 0
    totalCommentLines = 0
    filesMeasured = 0

    for filePath in sourceFiles:
        if filePath.suffix not in EXTENSION_LANGUAGE_MAP:
            continue
        sourceText = readTextSafely(filePath)
        if not sourceText:
            continue
        filesMeasured += 1
        relativePath = str(filePath.relative_to(basePath))
        lines = sourceText.splitlines()
        lineCount = len(lines)
        totalLines += lineCount
        totalCommentLines += sum(
            1 for line in lines if line.strip().startswith(("#", "//", "*", "/*"))
        )

        if lineCount > LARGE_FILE_LINE_THRESHOLD:
            issues.append(
                {
                    "file": relativePath,
                    "kind": "large_file",
                    "detail": f"{lineCount} lines (> {LARGE_FILE_LINE_THRESHOLD}).",
                }
            )

        complexityScore = _approximateComplexity(sourceText)
        if complexityScore > HIGH_COMPLEXITY_THRESHOLD:
            issues.append(
                {
                    "file": relativePath,
                    "kind": "high_complexity",
                    "detail": f"approx. complexity {complexityScore} (> {HIGH_COMPLEXITY_THRESHOLD}).",
                }
            )

    hasTests = _hasTests(sourceFiles)
    commentDensity = (totalCommentLines / totalLines) if totalLines else 0.0

    # Compute a health score starting from 100 and subtracting penalties.
    healthScore = 100
    healthScore -= min(40, len(issues) * 4)
    if not hasTests:
        healthScore -= 20
    if commentDensity < 0.02:
        healthScore -= 10
    healthScore = max(0, healthScore)

    if not hasTests:
        issues.append(
            {
                "file": "(repository)",
                "kind": "no_tests",
                "detail": "No test files detected.",
            }
        )

    qualityResult = {
        "health_score": healthScore,
        "has_tests": hasTests,
        "files_measured": filesMeasured,
        "issues": issues,
        "metrics": {
            "total_lines": totalLines,
            "comment_density": round(commentDensity, 4),
            "issue_count": len(issues),
        },
    }
    storeResult(str(basePath), "quality", qualityResult)
    return qualityResult
