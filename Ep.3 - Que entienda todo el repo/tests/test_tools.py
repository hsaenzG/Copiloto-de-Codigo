"""Unit tests for each tool.

Network- and LLM-dependent behavior is exercised only for graceful handling;
no test requires AWS credentials or internet access.
"""

from __future__ import annotations

from pathlib import Path

from code_copilot.tools.dependencies import (
    _parsePackageJson,
    _parseRequirementsTxt,
    scan_dependencies,
)
from code_copilot.tools.explain import explain_codebase
from code_copilot.tools.ingest import ingest_repository
from code_copilot.tools.quality import assess_quality
from code_copilot.tools.recommend import recommend_improvements
from code_copilot.tools.report import generate_report
from code_copilot.tools.sast import scan_sast
from code_copilot.tools.secrets import scan_secrets
from code_copilot.tools.structure import analyze_structure


def test_ingestRepository_localPath(callTool, sampleRepo: Path):
    result = callTool(ingest_repository, source=str(sampleRepo))
    assert result["file_count"] >= 3
    assert result["is_remote"] is False
    assert "Python" in result["languages"]
    assert result["package_managers"].get("requirements.txt") == "pip"


def test_ingestRepository_missingPath(callTool):
    result = callTool(ingest_repository, source="/no/such/path/xyz")
    assert "error" in result


def test_analyzeStructure_findsSymbolsAndEntryPoint(callTool, sampleRepo: Path):
    result = callTool(analyze_structure, repo_path=str(sampleRepo))
    assert result["files_analyzed"] >= 2
    assert "main.py" in result["entry_points"]
    assert result["symbol_count"] >= 1


def test_scanSast_detectsEval(callTool, sampleRepo: Path):
    result = callTool(scan_sast, repo_path=str(sampleRepo))
    # Either Semgrep or the builtin heuristics should flag eval().
    rules = " ".join(finding.get("rule", "") for finding in result["findings"])
    descriptions = " ".join(finding.get("description", "") for finding in result["findings"])
    assert "eval" in (rules + descriptions).lower()


def test_scanSecrets_redactsAwsKey(callTool, sampleRepo: Path):
    result = callTool(scan_secrets, repo_path=str(sampleRepo))
    assert result["finding_count"] >= 1
    for finding in result["findings"]:
        # The full example key must never appear in the output.
        assert "IOSFODNN7EXAMPLE" not in finding["redacted"]


def test_assessQuality_returnsScoreAndDetectsTests(callTool, sampleRepo: Path):
    result = callTool(assess_quality, repo_path=str(sampleRepo))
    assert 0 <= result["health_score"] <= 100
    assert result["has_tests"] is True


def test_parseRequirementsTxt_pinnedOnly():
    parsed = _parseRequirementsTxt("requests==2.31.0\n-e .\nflask==2.0.1\n")
    assert ("requests", "2.31.0") in parsed
    assert ("flask", "2.0.1") in parsed
    assert len(parsed) == 2


def test_parsePackageJson():
    parsed = _parsePackageJson('{"dependencies": {"express": "^4.18.2"}}')
    assert ("express", "4.18.2") in parsed


def test_scanDependencies_noManifests(callTool, tmp_path: Path):
    emptyRepo = tmp_path / "empty"
    emptyRepo.mkdir()
    result = callTool(scan_dependencies, repo_path=str(emptyRepo))
    assert result["dependency_count"] == 0
    assert result["vulnerabilities"] == []


def test_explainCodebase_gracefulWithoutCredentials(callTool, sampleRepo: Path):
    # Seed the state store as the producing tools would.
    from code_copilot.state import storeResult

    storeResult(str(sampleRepo), "ingest", {"languages": {"Python": 1}})
    storeResult(str(sampleRepo), "structure", {"modules": [], "entry_points": []})
    # No real Bedrock call succeeds in CI; the tool must not raise.
    result = callTool(explain_codebase, repo_path=str(sampleRepo))
    assert "explanation" in result  # present even on error path


def test_recommendImprovements_gracefulWithoutCredentials(callTool, sampleRepo: Path):
    from code_copilot.state import storeResult

    storeResult(str(sampleRepo), "quality", {"health_score": 80, "issues": []})
    result = callTool(recommend_improvements, repo_path=str(sampleRepo))
    assert "recommendations" in result


def test_generateReport_assemblesMarkdown(callTool, sampleRepo: Path, tmp_path: Path):
    from code_copilot.state import storeResult

    # Seed the store with every result generate_report reads.
    storeResult(str(sampleRepo), "ingest", {"repo_path": str(sampleRepo), "file_count": 3, "languages": {"Python": 3}})
    storeResult(str(sampleRepo), "explanation", "This project does X.")
    storeResult(str(sampleRepo), "sast", {"engine": "builtin", "findings": []})
    storeResult(str(sampleRepo), "dependencies", {"vulnerabilities": []})
    storeResult(str(sampleRepo), "secrets", {"findings": []})
    storeResult(str(sampleRepo), "quality", {"health_score": 88, "has_tests": True, "issues": []})
    storeResult(str(sampleRepo), "recommendations", "1. Do the thing.")

    reportPath = tmp_path / "report.md"
    result = callTool(
        generate_report,
        repo_path=str(sampleRepo),
        output_path=str(reportPath),
    )
    assert "# Code Copilot Report" in result["markdown"]
    assert reportPath.exists()
    assert result["written_to"] == str(reportPath)
