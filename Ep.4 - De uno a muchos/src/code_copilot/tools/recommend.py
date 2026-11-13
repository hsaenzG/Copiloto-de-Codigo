"""Tool: recommend_improvements.

Aggregates all findings (SAST, SCA, secrets, quality) from the analysis state
store and uses the LLM to produce a prioritized, actionable list of
recommendations ordered by impact and effort.

Reads results from the shared store keyed by ``repo_path`` so the model only
needs to emit a single short argument, avoiding malformed tool-use sequences on
large payloads.
"""

from __future__ import annotations

import json

from strands import tool

from code_copilot.model import runModelPrompt
from code_copilot.state import getResult, storeResult


def _buildRecommendPrompt(
    sastResult: dict, dependenciesResult: dict, secretsResult: dict, qualityResult: dict
) -> str:
    """Build a size-bounded prompt summarizing all findings for the LLM."""
    sastFindings = sastResult.get("findings", [])[:30]
    vulnerabilities = dependenciesResult.get("vulnerabilities", [])[:30]
    secretFindings = secretsResult.get("findings", [])[:30]
    qualityIssues = qualityResult.get("issues", [])[:30]
    healthScore = qualityResult.get("health_score", "n/a")

    return (
        "You are triaging findings for a codebase. Produce a prioritized list "
        "of actionable recommendations.\n\n"
        f"Health score (0-100): {healthScore}\n"
        f"SAST findings: {json.dumps(sastFindings)[:3000]}\n"
        f"Dependency vulnerabilities: {json.dumps(vulnerabilities)[:2000]}\n"
        f"Secret findings (already redacted): {json.dumps(secretFindings)[:1500]}\n"
        f"Quality issues: {json.dumps(qualityIssues)[:2000]}\n\n"
        "Return an ordered markdown list. For each item include: the problem, "
        "why it matters, a concrete recommendation, and an estimated effort "
        "(low/medium/high). Order by severity and impact first. Do not include "
        "any secret values."
    )


@tool
def recommend_improvements(repo_path: str) -> dict:
    """Consolidate all findings into prioritized, actionable recommendations.

    Reads the stored SAST, dependency, secret and quality results for
    ``repo_path`` from the analysis state store.

    Args:
        repo_path: Path to the ingested repository on disk.

    Returns:
        A dict with keys: recommendations (markdown text) or, on failure, error.
    """
    sastResult = getResult(repo_path, "sast", {}) or {}
    dependenciesResult = getResult(repo_path, "dependencies", {}) or {}
    secretsResult = getResult(repo_path, "secrets", {}) or {}
    qualityResult = getResult(repo_path, "quality", {}) or {}

    promptText = _buildRecommendPrompt(
        sastResult, dependenciesResult, secretsResult, qualityResult
    )
    try:
        recommendationsText = runModelPrompt(promptText, temperature=0.2, maxTokens=1800)
    except Exception as recommendError:  # noqa: BLE001 - surface LLM/credential failure
        return {
            "error": f"LLM recommendation failed: {recommendError}",
            "recommendations": "",
        }

    storeResult(repo_path, "recommendations", recommendationsText)
    return {"recommendations": recommendationsText}
