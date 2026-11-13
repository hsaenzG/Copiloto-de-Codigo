"""Tool: scan_secrets.

Detects hardcoded secrets (API keys, tokens, private keys) using a set of
regex detectors. The full secret value is NEVER returned or logged; findings
reference only the type, location, and a redacted preview.
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
    redactSecret,
)

# Each detector: (secretType, compiled regex). The first capturing group, when
# present, is treated as the secret value for redaction.
_SECRET_DETECTORS = [
    ("AWS Access Key ID", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("AWS Secret Access Key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})")),
    ("GitHub Token", re.compile(r"\b(ghp_[A-Za-z0-9]{36})\b")),
    ("Slack Token", re.compile(r"\b(xox[baprs]-[A-Za-z0-9\-]{10,})\b")),
    ("Google API Key", re.compile(r"\b(AIza[0-9A-Za-z\-_]{35})\b")),
    ("Private Key Block", re.compile(r"(-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----)")),
    ("Generic API Key Assignment", re.compile(r"(?i)(?:api[_-]?key|secret|token)\s*[=:]\s*['\"]([A-Za-z0-9\-_]{16,})['\"]")),
]


@tool
def scan_secrets(repo_path: str) -> dict:
    """Scan the repository for hardcoded secrets.

    Uses regex detectors for common credential formats. The secret value is
    always redacted in the output; only its type, file, line, and a masked
    preview are returned.

    Args:
        repo_path: Path to the ingested repository on disk.

    Returns:
        A dict with keys: findings (list of {file, line, type, redacted}) and
        finding_count.
    """
    basePath = Path(repo_path).expanduser().resolve()
    if not basePath.is_dir():
        return {"error": f"repo_path is not a directory: {basePath}"}

    findings: list[dict] = []
    for filePath in iterSourceFiles(basePath):
        # Also inspect common non-source config files for secrets.
        if filePath.suffix not in EXTENSION_LANGUAGE_MAP and filePath.suffix not in {
            ".env",
            ".yml",
            ".yaml",
            ".json",
            ".cfg",
            ".ini",
        }:
            continue
        sourceText = readTextSafely(filePath)
        if not sourceText:
            continue
        for lineNumber, lineText in enumerate(sourceText.splitlines(), start=1):
            for secretType, pattern in _SECRET_DETECTORS:
                match = pattern.search(lineText)
                if not match:
                    continue
                capturedValue = match.group(1) if match.groups() else match.group(0)
                findings.append(
                    {
                        "file": str(filePath.relative_to(basePath)),
                        "line": lineNumber,
                        "type": secretType,
                        "redacted": redactSecret(capturedValue),
                    }
                )

    secretsResult = {"findings": findings, "finding_count": len(findings)}
    storeResult(str(basePath), "secrets", secretsResult)
    return secretsResult
