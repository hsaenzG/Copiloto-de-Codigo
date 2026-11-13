"""Episode 4: the agent team (Strands Graph) runs offline with scripted models."""
from __future__ import annotations

from pathlib import Path

import pytest

from code_copilot import state, team
from code_copilot.team import (
    DOCS_NODE,
    INGEST_NODE,
    REVIEW_NODE,
    SECURITY_NODE,
    buildSpecialists,
    buildTeam,
    runTeam,
    writeDegradedReport,
)
from code_copilot.tools import ingest_repository
from fakes import ScriptedModel


@pytest.fixture(autouse=True)
def _cleanState():
    """Each test starts with an empty shared analysis state."""
    state.restore({})
    yield
    state.restore({})


def _scriptedSpecialists(
    repoPath: str,
    reportPath: str,
    securityError: Exception | None = None,
    securityDelaySeconds: float = 0.0,
):
    """Build the real specialists, each driven by a scripted fake model."""
    scripts = {
        INGEST_NODE: ScriptedModel(
            [("tool", "ingest_repository", {"source": repoPath}), ("text", repoPath)]
        ),
        SECURITY_NODE: ScriptedModel(
            [
                ("tool", "scan_sast", {"repo_path": repoPath}),
                ("tool", "scan_secrets", {"repo_path": repoPath}),
                ("text", "security summary"),
            ],
            error=securityError,
            delaySeconds=securityDelaySeconds,
        ),
        REVIEW_NODE: ScriptedModel(
            [
                ("tool", "analyze_structure", {"repo_path": repoPath}),
                ("tool", "assess_quality", {"repo_path": repoPath}),
                ("text", "review summary"),
            ]
        ),
        DOCS_NODE: ScriptedModel(
            [
                ("tool", "generate_report", {"repo_path": repoPath, "output_path": reportPath}),
                ("text", "report ready"),
            ]
        ),
    }
    pending = iter(scripts.values())
    return buildSpecialists(modelFactory=lambda **_: next(pending))


def test_buildSpecialistsGivesEachAgentOnlyItsTools():
    specialists = buildSpecialists(modelFactory=lambda **_: ScriptedModel())
    toolNames = {nodeId: set(agent.tool_names) for nodeId, agent in specialists.items()}
    assert toolNames[INGEST_NODE] == {"ingest_repository"}
    assert toolNames[SECURITY_NODE] == {"scan_sast", "scan_dependencies", "scan_secrets"}
    assert toolNames[REVIEW_NODE] == {"analyze_structure", "assess_quality"}
    assert toolNames[DOCS_NODE] == {"explain_codebase", "recommend_improvements", "generate_report"}


def test_teamRunsSpecialistsInParallelAndDocsLast(sampleRepo: Path, tmp_path: Path):
    reportPath = str(tmp_path / "report.md")
    graph = buildTeam(_scriptedSpecialists(str(sampleRepo), reportPath))

    teamResult = runTeam(str(sampleRepo), reportPath, graph=graph)

    assert teamResult.isSuccess
    assert teamResult.executionOrder[0] == INGEST_NODE
    assert set(teamResult.executionOrder[1:3]) == {SECURITY_NODE, REVIEW_NODE}
    assert teamResult.executionOrder[-1] == DOCS_NODE
    assert teamResult.executionOrder.count(DOCS_NODE) == 1
    report = Path(reportPath).read_text(encoding="utf-8")
    assert "AWS Access Key" in report or "Secrets" in report
    assert "Health score" in report


def test_failedSpecialistStopsGraphAndWritesPartialReport(sampleRepo: Path, tmp_path: Path):
    reportPath = str(tmp_path / "partial.md")
    graph = buildTeam(
        _scriptedSpecialists(
            str(sampleRepo), reportPath, securityError=RuntimeError("model call failed")
        )
    )

    teamResult = runTeam(str(sampleRepo), reportPath, graph=graph)

    assert teamResult.status == "FAILED"
    assert SECURITY_NODE in teamResult.failedNodes
    assert INGEST_NODE in teamResult.completedNodes
    assert DOCS_NODE not in teamResult.executionOrder
    assert "model call failed" in teamResult.error
    assert teamResult.degradedReportPath == reportPath
    partialReport = Path(reportPath).read_text(encoding="utf-8")
    assert "Team run incomplete: security failed" in partialReport


def test_partialReportUsesTheRequestedRepoWhenStateHoldsSeveral(
    sampleRepo: Path, tmp_path: Path, callTool
):
    otherRepo = tmp_path / "other_repo"
    otherRepo.mkdir()
    (otherRepo / "app.py").write_text("print('other')\n", encoding="utf-8")
    callTool(ingest_repository, source=str(otherRepo))  # ingested first
    callTool(ingest_repository, source=str(sampleRepo))

    reportPath = writeDegradedReport(str(sampleRepo), "security failed", str(tmp_path / "p.md"))

    report = Path(reportPath).read_text(encoding="utf-8")
    assert str(sampleRepo.resolve()) in report
    assert str(otherRepo.resolve()) not in report


def test_nodeTimeoutRaisesAndWritesPartialReport(sampleRepo: Path, tmp_path: Path, monkeypatch):
    """Python: a node timeout raises from the graph call (asyncio.wait_for)."""
    monkeypatch.setattr(team, "NODE_TIMEOUT_SECONDS", 0.2)
    reportPath = str(tmp_path / "node-timeout.md")
    graph = buildTeam(
        _scriptedSpecialists(str(sampleRepo), reportPath, securityDelaySeconds=2)
    )

    teamResult = runTeam(str(sampleRepo), reportPath, graph=graph)

    assert teamResult.status == "FAILED"
    assert "timed out" in teamResult.error
    assert teamResult.degradedReportPath == reportPath


def test_executionTimeoutReturnsFailedAndWritesPartialReport(
    sampleRepo: Path, tmp_path: Path, monkeypatch
):
    """Python: the execution timeout is checked between batches and returns FAILED
    without raising, so runTeam must handle a returned failure too."""
    monkeypatch.setattr(team, "EXECUTION_TIMEOUT_SECONDS", 0.5)
    reportPath = str(tmp_path / "exec-timeout.md")
    graph = buildTeam(
        _scriptedSpecialists(str(sampleRepo), reportPath, securityDelaySeconds=1)
    )

    teamResult = runTeam(str(sampleRepo), reportPath, graph=graph)

    assert teamResult.status == "FAILED"
    assert DOCS_NODE not in teamResult.executionOrder
    assert teamResult.error
    assert teamResult.degradedReportPath == reportPath
    partialReport = Path(reportPath).read_text(encoding="utf-8")
    assert "not run: docs" in partialReport
