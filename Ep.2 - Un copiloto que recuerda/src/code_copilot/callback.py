"""Colored callback handler for the Strands agent.

Strands invokes a callback handler with streaming events as the agent thinks,
emits text, and calls tools. This handler renders:

- the agent's natural-language text in the "agent" color, and
- each tool invocation with a bracketed label in that tool's own color.

The handler is defensive: Strands' event payloads vary across versions, so it
inspects the keyword arguments it receives and renders whatever it recognizes,
ignoring the rest.
"""

from __future__ import annotations

import sys

from code_copilot.colors import colorize, dim, formatToolLabel


class ColoredCallbackHandler:
    """Render agent output and tool usage with distinct colors.

    Instances are callable and are passed as ``callback_handler`` to the
    Strands ``Agent``.
    """

    def __init__(self) -> None:
        # Track the last tool announced so repeated tool-use deltas in the same
        # turn are not announced multiple times.
        self._lastToolName: str | None = None
        self._agentTextStarted = False

    def __call__(self, **eventData) -> None:
        """Handle a single streaming event from the agent."""
        # 1. Streaming natural-language text from the model.
        textChunk = eventData.get("data")
        if isinstance(textChunk, str) and textChunk:
            if not self._agentTextStarted:
                sys.stdout.write(colorize("agent> ", "agent", bold=True))
                self._agentTextStarted = True
            sys.stdout.write(colorize(textChunk, "agent"))
            sys.stdout.flush()

        # 2. A tool is being invoked. Strands exposes the current tool via the
        #    "current_tool_use" event payload.
        currentToolUse = eventData.get("current_tool_use")
        if isinstance(currentToolUse, dict):
            toolName = currentToolUse.get("name")
            if toolName and toolName != self._lastToolName:
                self._lastToolName = toolName
                self._finishAgentLine()
                sys.stdout.write(
                    f"\n{formatToolLabel(toolName)} {dim('running...')}\n"
                )
                sys.stdout.flush()

        # 3. Reset tool tracking at the end of a turn.
        if eventData.get("complete") or eventData.get("stop"):
            self._finishAgentLine()
            self._lastToolName = None

    def _finishAgentLine(self) -> None:
        """Emit a newline after streamed agent text so tool labels align."""
        if self._agentTextStarted:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._agentTextStarted = False
