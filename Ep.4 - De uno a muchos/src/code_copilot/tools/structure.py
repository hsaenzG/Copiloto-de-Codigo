"""Tool: analyze_structure.

Extracts a structural map of the repository using tree-sitter when available:
modules, functions/classes, imports, and likely entry points. Falls back to
lightweight regex heuristics when tree-sitter grammars are unavailable so the
tool never hard-fails.
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
)

# Filenames / patterns that usually indicate an application entry point.
_ENTRY_POINT_HINTS = (
    "main.py",
    "app.py",
    "manage.py",
    "index.js",
    "index.ts",
    "server.js",
    "server.ts",
    "main.go",
    "Main.java",
)

# Regex heuristics per language family for symbols and imports. These are only
# used as a fallback; tree-sitter is preferred when installed.
_SYMBOL_PATTERNS = {
    "Python": re.compile(r"^\s*(?:def|class)\s+(\w+)", re.MULTILINE),
    "JavaScript": re.compile(
        r"^\s*(?:function\s+(\w+)|class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()",
        re.MULTILINE,
    ),
    "TypeScript": re.compile(
        r"^\s*(?:function\s+(\w+)|class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()",
        re.MULTILINE,
    ),
    "Go": re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)", re.MULTILINE),
    "Java": re.compile(r"\b(?:class|interface)\s+(\w+)", re.MULTILINE),
}

_IMPORT_PATTERNS = {
    "Python": re.compile(r"^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import)", re.MULTILINE),
    "JavaScript": re.compile(r"""(?:import[^'"]*['"]([^'"]+)['"]|require\(['"]([^'"]+)['"]\))""", re.MULTILINE),
    "TypeScript": re.compile(r"""import[^'"]*['"]([^'"]+)['"]""", re.MULTILINE),
    "Go": re.compile(r'"([\w./\-]+)"', re.MULTILINE),
    "Java": re.compile(r"^\s*import\s+([\w.]+);", re.MULTILINE),
}


def _loadTreeSitterParser(languageName: str):
    """Load a tree-sitter parser for a language, or None if unavailable.

    Uses the per-language grammar packages (tree-sitter-python,
    tree-sitter-javascript). Returns None when the core library or the specific
    grammar is not installed, so the caller can fall back to regex heuristics.
    """
    try:
        from tree_sitter import Language, Parser
    except Exception:
        return None

    try:
        if languageName == "Python":
            import tree_sitter_python as grammarModule
        elif languageName in ("JavaScript", "TypeScript"):
            import tree_sitter_javascript as grammarModule
        else:
            return None
    except Exception:
        return None

    try:
        language = Language(grammarModule.language())
        return Parser(language)
    except Exception:
        return None


def _tryTreeSitterSymbols(sourceText: str, languageName: str) -> list[str] | None:
    """Attempt to extract top-level symbol names with tree-sitter.

    Returns None when tree-sitter or the grammar is unavailable, signaling the
    caller to fall back to regex heuristics.
    """
    parser = _loadTreeSitterParser(languageName)
    if parser is None:
        return None

    try:
        tree = parser.parse(sourceText.encode("utf-8"))
    except Exception:
        return None

    symbolNames: list[str] = []
    definitionNodeTypes = {
        "function_definition",
        "class_definition",
        "function_declaration",
        "method_declaration",
        "class_declaration",
    }

    def visit(node) -> None:
        if node.type in definitionNodeTypes:
            nameNode = node.child_by_field_name("name")
            if nameNode is not None:
                symbolNames.append(nameNode.text.decode("utf-8", errors="ignore"))
        for childNode in node.children:
            visit(childNode)

    visit(tree.root_node)
    return symbolNames


def _extractSymbols(sourceText: str, languageName: str) -> list[str]:
    """Extract symbol names, preferring tree-sitter then regex fallback."""
    treeSitterResult = _tryTreeSitterSymbols(sourceText, languageName)
    if treeSitterResult is not None:
        return treeSitterResult

    pattern = _SYMBOL_PATTERNS.get(languageName)
    if pattern is None:
        return []
    symbolNames: list[str] = []
    for matchGroups in pattern.findall(sourceText):
        if isinstance(matchGroups, tuple):
            symbolNames.extend(name for name in matchGroups if name)
        elif matchGroups:
            symbolNames.append(matchGroups)
    return symbolNames


def _extractImports(sourceText: str, languageName: str) -> list[str]:
    """Extract imported module names using regex heuristics."""
    pattern = _IMPORT_PATTERNS.get(languageName)
    if pattern is None:
        return []
    importNames: list[str] = []
    for matchGroups in pattern.findall(sourceText):
        if isinstance(matchGroups, tuple):
            importNames.extend(name for name in matchGroups if name)
        elif matchGroups:
            importNames.append(matchGroups)
    return importNames


@tool
def analyze_structure(repo_path: str, max_files: int = 400) -> dict:
    """Analyze the structure of a repository.

    Builds a structural map of modules, top-level symbols (functions/classes),
    imports and likely entry points. Uses tree-sitter when grammars are
    available and falls back to regex heuristics otherwise.

    Args:
        repo_path: Path to the ingested repository on disk.
        max_files: Maximum number of files to analyze (bounds runtime).

    Returns:
        A dict with keys: modules (per-file symbols and imports),
        entry_points, symbol_count, and import_count.
    """
    basePath = Path(repo_path).expanduser().resolve()
    if not basePath.is_dir():
        return {"error": f"repo_path is not a directory: {basePath}"}

    sourceFiles = iterSourceFiles(basePath)[:max_files]
    modules: list[dict] = []
    entryPoints: list[str] = []
    totalSymbolCount = 0
    totalImportCount = 0

    for filePath in sourceFiles:
        languageName = EXTENSION_LANGUAGE_MAP.get(filePath.suffix)
        if languageName is None:
            continue
        sourceText = readTextSafely(filePath)
        if not sourceText:
            continue

        symbolNames = _extractSymbols(sourceText, languageName)
        importNames = _extractImports(sourceText, languageName)
        totalSymbolCount += len(symbolNames)
        totalImportCount += len(importNames)

        relativePath = str(filePath.relative_to(basePath))
        if filePath.name in _ENTRY_POINT_HINTS:
            entryPoints.append(relativePath)

        modules.append(
            {
                "file": relativePath,
                "language": languageName,
                "symbols": symbolNames[:50],
                "imports": sorted(set(importNames))[:50],
            }
        )

    structureSummary = {
        "modules": modules,
        "entry_points": entryPoints,
        "symbol_count": totalSymbolCount,
        "import_count": totalImportCount,
        "files_analyzed": len(modules),
    }
    storeResult(str(basePath), "structure", structureSummary)
    return structureSummary
