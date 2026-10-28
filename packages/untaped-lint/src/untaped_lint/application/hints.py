"""The commands lint's messages name, spelled as the run mounts them.

The commands import the engine, so each is imported when a message is built.
"""

from __future__ import annotations

from untaped.sdk import command_line, run_hint


def rules_hint() -> str:
    """``run `untaped lint rules```: where a failed or unread rule is looked up."""
    from untaped_lint.commands.lint import Rules  # noqa: PLC0415 - import cycle

    return run_hint(Rules.spelled())


def rules_line() -> str:
    """``untaped lint rules``, for a sentence that names it."""
    from untaped_lint.commands.lint import Rules  # noqa: PLC0415 - import cycle

    return command_line(Rules.spelled())


def select_hint(rule: str) -> str:
    """``run `untaped lint --select RULE```: rerun one rule."""
    from untaped_lint.commands.lint import Lint  # noqa: PLC0415 - import cycle

    return run_hint(Lint.spelled("--select", rule))


def lint_line(*args: str) -> str:
    """``untaped lint ARGS``, for a sentence that names it."""
    from untaped_lint.commands.lint import Lint  # noqa: PLC0415 - import cycle

    return command_line(Lint.spelled(*args))


def new_line() -> str:
    """``untaped lint new``, for a sentence that names it."""
    from untaped_lint.commands.lint import New  # noqa: PLC0415 - import cycle

    return command_line(New.spelled())
