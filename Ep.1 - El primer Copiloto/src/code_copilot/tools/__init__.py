"""Agent tools.

Each capability is exposed to the Strands agent as an ``@tool``. Tools are pure
with respect to the repository content: they only analyze code statically and
never execute it.
"""

from code_copilot.tools.ingest import ingest_repository
from code_copilot.tools.structure import analyze_structure
from code_copilot.tools.sast import scan_sast
from code_copilot.tools.dependencies import scan_dependencies
from code_copilot.tools.secrets import scan_secrets
from code_copilot.tools.quality import assess_quality
from code_copilot.tools.explain import explain_codebase
from code_copilot.tools.recommend import recommend_improvements
from code_copilot.tools.report import generate_report

ALL_TOOLS = [
    ingest_repository,
    analyze_structure,
    scan_sast,
    scan_dependencies,
    scan_secrets,
    assess_quality,
    explain_codebase,
    recommend_improvements,
    generate_report,
]

__all__ = [
    "ingest_repository",
    "analyze_structure",
    "scan_sast",
    "scan_dependencies",
    "scan_secrets",
    "assess_quality",
    "explain_codebase",
    "recommend_improvements",
    "generate_report",
    "ALL_TOOLS",
]
