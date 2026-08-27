"""Tool: scan_dependencies.

Software Composition Analysis (SCA). Parses dependency manifests and queries
the free OSV.dev API for known vulnerabilities per package+version. Designed
to degrade gracefully: parsing failures for one manifest never abort the scan,
and a network failure is reported as a limitation.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import requests
from strands import tool

from code_copilot.state import storeResult

# OSV.dev batch query endpoint (free, no auth required).
OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"

# OSV ecosystem names keyed by manifest file.
_ECOSYSTEM_BY_MANIFEST = {
    "requirements.txt": "PyPI",
    "pyproject.toml": "PyPI",
    "package.json": "npm",
    "go.mod": "Go",
}


def _parseRequirementsTxt(manifestText: str) -> list[tuple[str, str]]:
    """Parse ``name==version`` lines from a requirements.txt file."""
    dependencies: list[tuple[str, str]] = []
    for rawLine in manifestText.splitlines():
        line = rawLine.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        pinnedMatch = re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*([\w.\-]+)", line)
        if pinnedMatch:
            dependencies.append((pinnedMatch.group(1), pinnedMatch.group(2)))
    return dependencies


def _parsePackageJson(manifestText: str) -> list[tuple[str, str]]:
    """Parse dependencies and devDependencies from a package.json file."""
    dependencies: list[tuple[str, str]] = []
    try:
        parsed = json.loads(manifestText)
    except json.JSONDecodeError:
        return dependencies
    for sectionName in ("dependencies", "devDependencies"):
        for packageName, versionSpec in parsed.get(sectionName, {}).items():
            cleanedVersion = str(versionSpec).lstrip("^~>=< ").strip()
            if cleanedVersion:
                dependencies.append((packageName, cleanedVersion))
    return dependencies


def _parsePyprojectToml(manifestText: str) -> list[tuple[str, str]]:
    """Parse pinned dependencies from a pyproject.toml [project] table."""
    dependencies: list[tuple[str, str]] = []
    try:
        parsed = tomllib.loads(manifestText)
    except tomllib.TOMLDecodeError:
        return dependencies
    for requirement in parsed.get("project", {}).get("dependencies", []):
        pinnedMatch = re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*([\w.\-]+)", requirement)
        if pinnedMatch:
            dependencies.append((pinnedMatch.group(1), pinnedMatch.group(2)))
    return dependencies


def _parseGoMod(manifestText: str) -> list[tuple[str, str]]:
    """Parse ``require`` entries from a go.mod file."""
    dependencies: list[tuple[str, str]] = []
    for rawLine in manifestText.splitlines():
        line = rawLine.strip()
        requireMatch = re.match(r"^(?:require\s+)?([\w./\-]+)\s+v([\w.\-]+)", line)
        if requireMatch and "/" in requireMatch.group(1):
            dependencies.append((requireMatch.group(1), requireMatch.group(2)))
    return dependencies


_PARSERS = {
    "requirements.txt": _parseRequirementsTxt,
    "package.json": _parsePackageJson,
    "pyproject.toml": _parsePyprojectToml,
    "go.mod": _parseGoMod,
}


def _queryOsv(queries: list[dict]) -> list[dict]:
    """Send a batch query to OSV.dev and return the raw per-query results."""
    response = requests.post(OSV_BATCH_URL, json={"queries": queries}, timeout=30)
    response.raise_for_status()
    return response.json().get("results", [])


@tool
def scan_dependencies(repo_path: str) -> dict:
    """Scan project dependencies for known vulnerabilities (SCA).

    Parses supported manifests (requirements.txt, pyproject.toml, package.json,
    go.mod) and queries the OSV.dev API for known CVEs per package+version.

    Args:
        repo_path: Path to the ingested repository on disk.

    Returns:
        A dict with keys: manifests_scanned, dependency_count, vulnerabilities
        (list of {package, version, id, ecosystem}), and optionally limitation.
    """
    basePath = Path(repo_path).expanduser().resolve()
    if not basePath.is_dir():
        return {"error": f"repo_path is not a directory: {basePath}"}

    allQueries: list[dict] = []
    queryMetadata: list[tuple[str, str, str]] = []
    manifestsScanned: list[str] = []

    for manifestName, parser in _PARSERS.items():
        manifestPath = basePath / manifestName
        if not manifestPath.exists():
            continue
        manifestsScanned.append(manifestName)
        ecosystem = _ECOSYSTEM_BY_MANIFEST[manifestName]
        manifestText = manifestPath.read_text(encoding="utf-8", errors="ignore")
        for packageName, packageVersion in parser(manifestText):
            allQueries.append(
                {
                    "package": {"name": packageName, "ecosystem": ecosystem},
                    "version": packageVersion,
                }
            )
            queryMetadata.append((packageName, packageVersion, ecosystem))

    if not allQueries:
        emptyResult = {
            "manifests_scanned": manifestsScanned,
            "dependency_count": 0,
            "vulnerabilities": [],
        }
        storeResult(str(basePath), "dependencies", emptyResult)
        return emptyResult

    try:
        osvResults = _queryOsv(allQueries)
    except requests.RequestException as scaError:
        failedResult = {
            "manifests_scanned": manifestsScanned,
            "dependency_count": len(allQueries),
            "vulnerabilities": [],
            "limitation": f"OSV.dev query failed: {scaError}",
        }
        storeResult(str(basePath), "dependencies", failedResult)
        return failedResult

    vulnerabilities: list[dict] = []
    for (packageName, packageVersion, ecosystem), osvEntry in zip(queryMetadata, osvResults):
        for vulnerability in osvEntry.get("vulns", []):
            vulnerabilities.append(
                {
                    "package": packageName,
                    "version": packageVersion,
                    "id": vulnerability.get("id", ""),
                    "ecosystem": ecosystem,
                }
            )

    dependenciesResult = {
        "manifests_scanned": manifestsScanned,
        "dependency_count": len(allQueries),
        "vulnerabilities": vulnerabilities,
        "vulnerability_count": len(vulnerabilities),
    }
    storeResult(str(basePath), "dependencies", dependenciesResult)
    return dependenciesResult
