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
import logging
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
    argumentParser.add_argument(
        "--session-id",
        dest="session_id",
        default="",
        help=(
            "Conversation session id. Reusing the same id across runs recovers "
            "the prior history (long-term memory). Defaults to MEMORY_SESSION_ID "
            "or 'default'."
        ),
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


def _resolveSessionId(cliValue: str) -> str:
    """Resolve the session id from the CLI, environment, or default."""
    from code_copilot.config import DEFAULT_SESSION_ID
    from code_copilot.memory import sanitizeSessionId

    raw = cliValue or os.environ.get("MEMORY_SESSION_ID") or DEFAULT_SESSION_ID
    return sanitizeSessionId(raw)


def _runAgent(source: str, reportPath: str, sessionId: str) -> int:
    """Run the LLM-driven agent over the repository, with conversational memory.

    One turn: load history for ``sessionId`` -> prune to budget -> run the agent
    seeded with that history -> append the new exchange -> persist. The shared
    analysis state is persisted alongside the conversation so it survives runs.
    """
    from code_copilot import state
    from code_copilot.agent import buildAgent
    from code_copilot.config import buildSessionManager, loadMemoryConfig

    print(heading("Code Copilot — agent mode (Amazon Bedrock Nova Pro)"))

    memoryConfig = loadMemoryConfig()
    print(
        colorize(
            f"  Session: {sessionId} | backend={memoryConfig.backend} "
            f"| strategy={memoryConfig.strategy} | max_tokens={memoryConfig.maxTokens}",
            "system",
        )
    )

    try:
        sessionManager = buildSessionManager(memoryConfig)
    except ValueError as configError:
        print(colorize(f"Memory config error: {configError}", "error", bold=True))
        return 1

    # Long-term memory: recover prior conversation and shared state for this id.
    priorMessages = sessionManager.loadHistory(sessionId)
    statePath = Path(memoryConfig.directory) / f"session_{sessionId}" / "state.json"
    state.loadFrom(statePath)
    if priorMessages:
        print(
            colorize(
                f"  Recovered {len(priorMessages)} message(s) from prior session.",
                "system",
            )
        )

    try:
        agent = buildAgent(priorMessages=priorMessages)
    except Exception as buildError:  # noqa: BLE001
        print(colorize(f"Failed to initialize agent: {buildError}", "error", bold=True))
        print(colorize("Tip: try --offline to run without Bedrock.", "system"))
        return 1

    reportClause = f" Write the report to '{reportPath}'." if reportPath else ""
    userPromptText = (
        f"Analyze the repository at: {source}. Run the full workflow "
        f"(ingest, structure, SAST, dependencies, secrets, quality, explain, "
        f"recommend, report) and give me the final report.{reportClause}"
    )

    try:
        agentResult = agent(userPromptText)
    except Exception as runError:  # noqa: BLE001
        print(colorize(f"\nAgent run failed: {runError}", "error", bold=True))
        print(colorize("Tip: verify AWS credentials and Bedrock model access, "
                        "or use --offline.", "system"))
        return 1

    # End of turn: append the new exchange and persist conversation + state.
    userMessage = {"role": "user", "content": [{"text": userPromptText}]}
    assistantMessage = {"role": "assistant", "content": [{"text": str(agentResult)}]}
    sessionManager.appendExchange(sessionId, userMessage, assistantMessage)
    state.saveTo(statePath)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    # Load project .env (AWS_PROFILE, AWS_REGION, BEDROCK_MODEL_ID) if present.
    _loadDotEnv()

    # Surface memory cost logs (e.g. prune events) on stderr so the token
    # before/after of each pruning event is visible during the demo.
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    argumentParser = _buildArgumentParser()
    arguments = argumentParser.parse_args(argv)

    if arguments.offline:
        return _runOffline(arguments.source, arguments.report)
    sessionId = _resolveSessionId(arguments.session_id)
    return _runAgent(arguments.source, arguments.report, sessionId)


if __name__ == "__main__":
    sys.exit(main())
