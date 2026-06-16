"""Process-global identity of the running tool.

A tool process runs exactly one tool (set up by its ``run_tool`` composition
root). Its command — e.g. ``untaped-github`` — is recorded here so deep SDK
helpers can render command-aware guidance ("set it with ``untaped-github
config set token``") without threading the :class:`~untaped.tool.ToolSpec`
through every call site.

``None`` means no tool has registered: the legacy umbrella (``untaped``)
context, where guidance keeps its historical, fully-qualified form.
"""

from __future__ import annotations

_command: str | None = None


def set_tool_command(command: str | None) -> None:
    """Record the command of the tool running in this process."""
    global _command
    _command = command


def current_tool_command() -> str | None:
    """Return the running tool's command, or ``None`` in the umbrella context."""
    return _command


def reset_tool_command() -> None:
    """Clear the recorded tool command. Public for test isolation."""
    global _command
    _command = None
