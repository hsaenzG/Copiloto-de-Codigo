"""Tool: generate_report.

Assembles the final Markdown report from all prior outputs: summary,
explanation, security findings, code health, and prioritized recommendations.
Deterministic and side-effect-light (optionally writes the report to disk).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from strands import tool

from code_copilot.state import getResult


def _renderSecuritySection(sast: dict, dependencies: dict, secrets: dict) -> str:
    """Render the security findings section of the report."""
    lines = ["## Security Findings", ""]

    sastFindings = sast.get("findings", [])
    lines.append(f"### SAST ({sast.get('engine', 'n/a')}) — {len(sastFindings)} finding(s)")
    if sast.get("limitation"):
        lines.append(f"> Limitation: {sast['limitation']}")
    for finding in sastFindings[:25]:
        lines.append(
            f"- **{finding.get('severity', '?')}** `{finding.get('rule', '')}` "
            f"in `{finding.get('file', '')}:{finding.get('line', '?')}` — "
            f"{finding.get('description', '')}"
        )
    lines.append("")

    vulnerabilities = dependencies.get("vulnerabilities", [])
    lines.append(f"### Dependencies (SCA) — {len(vulnerabilities)} vulnerability(ies)")
    if dependencies.get("limitation"):
        lines.append(f"> Limitation: {dependencies['limitation']}")
    for vulnerability in vulnerabilities[:25]:
        lines.append(
            f"- `{vulnerability.get('package', '')}@{vulnerability.get('version', '')}` "
            f"({vulnerability.get('ecosystem', '')}) — {vulnerability.get('id', '')}"
        )
    lines.append("")

    secretFindings = secrets.get("findings", [])
    lines.append(f"### Secrets — {len(secretFindings)} potential secret(s)")
    for finding in secretFindings[:25]:
        # Values are already redacted by scan_secrets.
        lines.append(
            f"- **{finding.get('type', '')}** in "
            f"`{finding.get('file', '')}:{finding.get('line', '?')}` "
            f"(value `{finding.get('redacted', '')}`)"
        )
    lines.append("")
    return "\n".join(lines)


@tool
def generate_report(repo_path: str, output_path: str = "") -> dict:
    """Assemble the final Markdown report from all analysis outputs.

    Reads every stored result for ``repo_path`` (ingest, explanation, sast,
    dependencies, secrets, quality, recommendations) from the analysis state
    store, so the model only passes a short path argument.

    Args:
        repo_path: Path to the ingested repository on disk.
        output_path: Optional path to write the Markdown report to.

    Returns:
        A dict with keys: markdown (the full report) and, when written,
        written_to.
    """
    ingest_summary = getResult(repo_path, "ingest", {}) or {}
    explanation = getResult(repo_path, "explanation", "") or ""
    sast = getResult(repo_path, "sast", {}) or {}
    dependencies = getResult(repo_path, "dependencies", {}) or {}
    secrets = getResult(repo_path, "secrets", {}) or {}
    quality = getResult(repo_path, "quality", {}) or {}
    recommendations = getResult(repo_path, "recommendations", "") or ""

    generatedAt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    languages = ingest_summary.get("languages", {}) if ingest_summary else {}
    healthScore = quality.get("health_score", "n/a") if quality else "n/a"

    reportParts = [
        "# Code Copilot Report",
        f"_Generated {generatedAt}_",
        "",
        "## Overview",
        f"- Repository: `{ingest_summary.get('repo_path', 'n/a')}`",
        f"- Files analyzed: {ingest_summary.get('file_count', 'n/a')}",
        f"- Languages: {', '.join(f'{name} ({count})' for name, count in languages.items()) or 'n/a'}",
        f"- Health score: **{healthScore}/100**",
        "",
        "## Explanation",
        explanation or "_No explanation produced._",
        "",
        _renderSecuritySection(sast or {}, dependencies or {}, secrets or {}),
        "## Code Health",
        f"- Score: **{healthScore}/100**",
        f"- Has tests: {quality.get('has_tests', 'n/a') if quality else 'n/a'}",
        f"- Issues: {len(quality.get('issues', [])) if quality else 0}",
        "",
        "## Prioritized Recommendations",
        recommendations or "_No recommendations produced._",
        "",
    ]
    markdownReport = "\n".join(reportParts)

    result: dict = {"markdown": markdownReport}
    if output_path:
        targetPath = Path(output_path).expanduser()
        targetPath.parent.mkdir(parents=True, exist_ok=True)
        targetPath.write_text(markdownReport, encoding="utf-8")
        result["written_to"] = str(targetPath)
    return result
