"""Tool: explain_codebase.

Uses Amazon Bedrock (Nova Pro) to produce a natural-language explanation of
what the project does, its architecture, main flows, and how the pieces
connect.

To keep the model's tool-use arguments small (a single ``repo_path`` string),
the ingest and structure results are read from the shared analysis state store
rather than being passed in as large nested dictionaries.
"""

from __future__ import annotations

import json

from strands import tool

from code_copilot.model import runModelPrompt
from code_copilot.state import getResult, storeResult


def _buildExplainPrompt(ingestSummary: dict, structureSummary: dict) -> str:
    """Build the LLM prompt from bounded slices of the analysis outputs."""
    languages = ingestSummary.get("languages", {})
    entryPoints = structureSummary.get("entry_points", [])
    # Keep only a bounded sample of modules to control prompt size.
    moduleSample = structureSummary.get("modules", [])[:40]

    return (
        "Explain this software repository for a technical reader.\n\n"
        f"Languages (file counts): {json.dumps(languages)}\n"
        f"Package managers: {json.dumps(ingestSummary.get('package_managers', {}))}\n"
        f"Config files: {json.dumps(ingestSummary.get('config_files', []))}\n"
        f"Entry points: {json.dumps(entryPoints)}\n"
        f"Directory tree (partial):\n{ingestSummary.get('directory_tree', '')[:2000]}\n\n"
        f"Modules sample (file -> symbols/imports):\n{json.dumps(moduleSample)[:4000]}\n\n"
        "Produce:\n"
        "1. A 2-3 sentence executive summary of what the project does.\n"
        "2. The likely architecture and main runtime flows.\n"
        "3. Key technologies and how the pieces connect.\n"
        "Be concise and do not invent components that are not evidenced above."
    )


@tool
def explain_codebase(repo_path: str) -> dict:
    """Explain the codebase in natural language using the LLM.

    Reads the previously stored ingest_repository and analyze_structure results
    for ``repo_path`` from the analysis state store.

    Args:
        repo_path: Path to the ingested repository on disk.

    Returns:
        A dict with keys: explanation (markdown text) or, on failure, error.
    """
    ingestSummary = getResult(repo_path, "ingest", {}) or {}
    structureSummary = getResult(repo_path, "structure", {}) or {}

    promptText = _buildExplainPrompt(ingestSummary, structureSummary)
    try:
        explanationText = runModelPrompt(promptText, temperature=0.2, maxTokens=1500)
    except Exception as explainError:  # noqa: BLE001 - surface any LLM/credential failure
        return {
            "error": f"LLM explanation failed: {explainError}",
            "explanation": "",
        }

    storeResult(repo_path, "explanation", explanationText)
    return {"explanation": explanationText}
