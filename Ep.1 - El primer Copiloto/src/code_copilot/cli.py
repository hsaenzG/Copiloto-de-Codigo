"""Command-line entry point for the Code Copilot agent.

Runs from the terminal. Two modes:

- Default (agent) mode: drives the Strands + Bedrock agent, which decides how
  to use the tools. Requires AWS credentials with Bedrock access.
- ``--offline`` mode: runs the deterministic static pipeline without the LLM.
  Useful when Bedrock credentials are unavailable.

Agent responses are printed in one color and each tool in its own color.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from code_copilot.colors import colorize, heading


def _loadDotEnv(dotEnvPath: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE lines from a .env file into the environment.

    Existing environment variables take precedence and are not overwritten.
    Kept dependency-free so the app does not require python-dotenv.
    """
    if not dotEnvPath.exists():
        return
    for rawLine in dotEnvPath.read_text(encoding="utf-8").splitlines():
        line = rawLine.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keyName, _, rawValue = line.partition("=")
        keyName = keyName.strip()
        value = rawValue.strip().strip('"').strip("'")
        if keyName and keyName not in os.environ:
            os.environ[keyName] = value


def _buildArgumentParser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    argumentParser = argparse.ArgumentParser(
        prog="code-copilot",
        description="AI code copilot: ingest, evaluate, explain, scan and recommend.",
    )
    argumentParser.add_argument(
        "source",
        help="GitHub repository URL or local path to analyze.",
    )
    argumentParser.add_argument(
        "--offline",
        action="store_true",
        help="Run the static pipeline without calling the LLM (no AWS needed).",
    )
    argumentParser.add_argument(
        "--report",
        default="",
        help="Path to write the Markdown report to (optional).",
    )
    return argumentParser


def _runOffline(source: str, reportPath: str) -> int:
    """Run the offline static pipeline and print a colored summary."""
    from code_copilot.pipeline import runPipeline

    print(heading("Code Copilot — offline pipeline"))
    pipelineResult = runPipeline(source, reportPath=reportPath)

    if pipelineResult.get("error"):
        print(colorize(f"Error: {pipelineResult['error']}", "error", bold=True))
        return 1

    quality = pipelineResult["quality"]
    sast = pipelineResult["sast"]
    dependencies = pipelineResult["dependencies"]
    secrets = pipelineResult["secrets"]

    print()
    print(heading("Summary"))
    print(colorize(f"  Health score: {quality.get('health_score')}/100", "success"))
    print(colorize(f"  SAST findings: {sast.get('finding_count', 0)}", "warning"))
    print(
        colorize(
            f"  Dependency vulns: {dependencies.get('vulnerability_count', 0)}",
            "warning",
        )
    )
    print(colorize(f"  Potential secrets: {secrets.get('finding_count', 0)}", "warning"))

    reportInfo = pipelineResult["report"]
    if reportInfo.get("written_to"):
        print(colorize(f"  Report written to: {reportInfo['written_to']}", "system"))
    return 0


def _runAgent(source: str, reportPath: str) -> int:
    """Run the LLM-driven agent over the repository."""
    from code_copilot.agent import buildAgent

    print(heading("Code Copilot — agent mode (Amazon Bedrock Nova Pro)"))
    try:
        agent = buildAgent()
    except Exception as buildError:  # noqa: BLE001
        print(colorize(f"Failed to initialize agent: {buildError}", "error", bold=True))
        print(colorize("Tip: try --offline to run without Bedrock.", "system"))
        return 1

    reportClause = f" Write the report to '{reportPath}'." if reportPath else ""
    userPrompt = (
        f"Analyze the repository at: {source}. Run the full workflow "
        f"(ingest, structure, SAST, dependencies, secrets, quality, explain, "
        f"recommend, report) and give me the final report.{reportClause}"
    )

    try:
        agent(userPrompt)
    except Exception as runError:  # noqa: BLE001
        print(colorize(f"\nAgent run failed: {runError}", "error", bold=True))
        print(colorize("Tip: verify AWS credentials and Bedrock model access, "
                        "or use --offline.", "system"))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    # Load project .env (AWS_PROFILE, AWS_REGION, BEDROCK_MODEL_ID) if present.
    _loadDotEnv()

    argumentParser = _buildArgumentParser()
    arguments = argumentParser.parse_args(argv)

    if arguments.offline:
        return _runOffline(arguments.source, arguments.report)
    return _runAgent(arguments.source, arguments.report)


if __name__ == "__main__":
    sys.exit(main())
