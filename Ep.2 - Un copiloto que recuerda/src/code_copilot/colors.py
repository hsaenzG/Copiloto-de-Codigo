"""ANSI color helpers for terminal output.

The agent's natural-language responses and every tool are rendered in a
distinct color so the user can tell, at a glance, what the agent is saying
versus which tool is running. Colors are disabled automatically when the
output is not a TTY or when the ``NO_COLOR`` environment variable is set
(see https://no-color.org/).
"""

from __future__ import annotations

import os
import sys

# Raw ANSI SGR codes. Kept private; use the helpers below.
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

# Foreground colors (256-color palette for broad terminal support).
_COLORS = {
    "agent": "\033[38;5;39m",       # bright blue  -> agent natural-language text
    "tool": "\033[38;5;208m",       # orange       -> generic tool activity
    "system": "\033[38;5;245m",     # gray         -> framework / system notes
    "success": "\033[38;5;42m",     # green        -> success messages
    "warning": "\033[38;5;220m",    # yellow       -> warnings / degraded results
    "error": "\033[38;5;196m",      # red          -> errors
    "heading": "\033[38;5;213m",    # magenta/pink -> section headings
}

# A stable per-tool color palette. Each tool name maps to its own color so the
# same tool always appears in the same color across a run.
_TOOL_PALETTE = [
    "\033[38;5;208m",  # orange
    "\033[38;5;51m",   # cyan
    "\033[38;5;118m",  # lime
    "\033[38;5;201m",  # magenta
    "\033[38;5;226m",  # yellow
    "\033[38;5;99m",   # violet
    "\033[38;5;209m",  # salmon
    "\033[38;5;45m",   # teal
    "\033[38;5;214m",  # amber
]


def colorEnabled() -> bool:
    """Return whether ANSI colors should be emitted.

    Colors are disabled when ``NO_COLOR`` is set or when stdout is not a
    terminal (for example when output is piped to a file).
    """
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def colorize(text: str, colorName: str, *, bold: bool = False) -> str:
    """Wrap ``text`` in the ANSI codes for ``colorName``.

    Args:
        text: The text to colorize.
        colorName: A key from the semantic color map (agent, tool, error, ...).
        bold: Whether to render the text in bold.
    """
    if not colorEnabled():
        return text
    colorCode = _COLORS.get(colorName, "")
    prefix = (_BOLD if bold else "") + colorCode
    if not prefix:
        return text
    return f"{prefix}{text}{_RESET}"


def toolColor(toolName: str) -> str:
    """Return a stable ANSI color code for a given tool name.

    The color is chosen deterministically from the tool name so that a tool
    keeps the same color for the whole session.
    """
    if not colorEnabled():
        return ""
    paletteIndex = sum(ord(character) for character in toolName) % len(_TOOL_PALETTE)
    return _TOOL_PALETTE[paletteIndex]


def formatToolLabel(toolName: str) -> str:
    """Format a bracketed, colored label for a tool, e.g. ``[scan_sast]``."""
    label = f"[{toolName}]"
    if not colorEnabled():
        return label
    return f"{_BOLD}{toolColor(toolName)}{label}{_RESET}"


def heading(text: str) -> str:
    """Format a bold section heading."""
    return colorize(text, "heading", bold=True)


def dim(text: str) -> str:
    """Render secondary/less-important text in a dim style."""
    if not colorEnabled():
        return text
    return f"{_DIM}{text}{_RESET}"
