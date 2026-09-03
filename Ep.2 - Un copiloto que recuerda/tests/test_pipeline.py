"""End-to-end test of the offline pipeline over the sample repository."""

from __future__ import annotations

from pathlib import Path

from code_copilot.colors import colorize, toolColor
from code_copilot.pipeline import runPipeline


def test_runPipeline_endToEnd(sampleRepo: Path, tmp_path: Path):
    reportPath = tmp_path / "report.md"
    result = runPipeline(str(sampleRepo), reportPath=str(reportPath))

    assert "error" not in result
    # Every stage produced output.
    assert result["ingest"]["file_count"] >= 3
    assert result["structure"]["files_analyzed"] >= 2
    assert "findings" in result["sast"]
    assert "vulnerabilities" in result["dependencies"]
    assert result["secrets"]["finding_count"] >= 1
    assert 0 <= result["quality"]["health_score"] <= 100

    # Report was assembled and written.
    assert "# Code Copilot Report" in result["report"]["markdown"]
    assert reportPath.exists()


def test_toolColors_areStablePerTool():
    # The same tool name always maps to the same color code.
    assert toolColor("scan_sast") == toolColor("scan_sast")


def test_colorize_disabledWhenNoColor(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    # With NO_COLOR set, colorize returns the text unchanged.
    assert colorize("hello", "agent") == "hello"
