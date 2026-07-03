"""Process-global identity of the running tool.

A tool process runs exactly one tool (set up by its ``run_tool`` composition
root). Its command — e.g. ``untaped-github`` — is recorded here so deep SDK
helpers can render command-aware guidance ("set it with ``untaped-github
config set token``") without threading the :class:`~untaped.tool.ToolSpec`
through every call site.

``None`` means no tool has registered (e.g. the SDK used without ``run_tool``),
where guidance falls back to a neutral ``<tool>`` placeholder.
Stored in a ``ContextVar`` so the effect is scoped to the current execution
context rather than truly process-global.
"""

from __future__ import annotations

from contextvars import ContextVar

_command: ContextVar[str | None] = ContextVar("untaped_tool_command", default=None)


def set_tool_command(command: str | None) -> None:
    """Record the command of the tool running in this process."""
    _command.set(command)


def current_tool_command() -> str | None:
    """Return the running tool's command, or ``None`` when no tool is registered."""
    return _command.get()


def reset_tool_command() -> None:
    """Clear the recorded tool command. Public for test isolation."""
    _command.set(None)
