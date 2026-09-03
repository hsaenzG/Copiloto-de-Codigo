"""Deterministic analysis pipeline (no LLM).

Runs the static tools in the prescribed order and returns the aggregated
findings. This path does not require Bedrock credentials, so it is used for
offline runs, tests, and as the data source for report generation. The
LLM-backed explanation and recommendations are attempted separately and
degrade gracefully when credentials are unavailable.
"""

from __future__ import annotations

from code_copilot.colors import colorize, formatToolLabel
from code_copilot.state import storeResult
from code_copilot.tools.dependencies import scan_dependencies
from code_copilot.tools.ingest import ingest_repository
from code_copilot.tools.quality import assess_quality
from code_copilot.tools.report import generate_report
from code_copilot.tools.sast import scan_sast
from code_copilot.tools.secrets import scan_secrets
from code_copilot.tools.structure import analyze_structure


def _callTool(toolObject, **keywordArguments) -> dict:
    """Invoke a Strands @tool directly and return its dict result.

    Strands tool objects wrap the original function; ``.original`` or
    ``__wrapped__`` exposes it. We fall back to calling the object directly.
    """
    underlyingFunction = (
        getattr(toolObject, "original", None)
        or getattr(toolObject, "__wrapped__", None)
        or toolObject
    )
    return underlyingFunction(**keywordArguments)


def _announce(toolName: str) -> None:
    """Print a colored label announcing a tool is running."""
    print(f"{formatToolLabel(toolName)} {colorize('running...', 'system')}")


def runPipeline(source: str, reportPath: str = "") -> dict:
    """Run the full static pipeline over ``source``.

    Args:
        source: GitHub URL or local path to the repository.
        reportPath: Optional path to write the Markdown report to.

    Returns:
        A dict aggregating every tool output plus the assembled report under
        the "report" key.
    """
    _announce("ingest_repository")
    ingestSummary = _callTool(ingest_repository, source=source)
    if ingestSummary.get("error"):
        return {"error": ingestSummary["error"]}

    repoPath = ingestSummary["repo_path"]

    _announce("analyze_structure")
    structureSummary = _callTool(analyze_structure, repo_path=repoPath)

    _announce("scan_sast")
    sastResult = _callTool(scan_sast, repo_path=repoPath)

    _announce("scan_dependencies")
    dependenciesResult = _callTool(scan_dependencies, repo_path=repoPath)

    _announce("scan_secrets")
    secretsResult = _callTool(scan_secrets, repo_path=repoPath)

    _announce("assess_quality")
    qualityResult = _callTool(assess_quality, repo_path=repoPath)

    # In offline mode there is no LLM synthesis; store placeholders so the
    # report renders those sections clearly.
    storeResult(repoPath, "explanation", "(LLM explanation skipped in offline pipeline mode.)")
    storeResult(
        repoPath,
        "recommendations",
        "(LLM recommendations skipped in offline pipeline mode.)",
    )

    _announce("generate_report")
    reportResult = _callTool(
        generate_report,
        repo_path=repoPath,
        output_path=reportPath,
    )

    return {
        "ingest": ingestSummary,
        "structure": structureSummary,
        "sast": sastResult,
        "dependencies": dependenciesResult,
        "secrets": secretsResult,
        "quality": qualityResult,
        "report": reportResult,
    }
