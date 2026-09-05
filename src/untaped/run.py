"""Composition root: turn a tool's cyclopts app + ToolSpec into a runnable CLI.

``run_tool(app, spec)`` is a tool's ``main()``. It registers the tool's
settings and the built-in profiles layout, mounts the ``config`` / ``profile``
/ ``skills`` command groups, wires position-independent ``--profile`` /
``--verbose`` root options (usable in any token position, like the retired
hub), overrides the program name to the tool's command, wires ``--version`` to
lazy installed-distribution metadata, registers shell completion, and runs
under untaped's error-reporting contract.

``build_tool_app`` is the wiring half — it returns the configured app so
callers (and tests) can drive ``app.meta`` directly without running it.

The leading-consume + strip-on-unknown machinery gives ``--profile`` /
``--verbose`` position-independence: a tool's root options are a fixed pair
backed by direct handler callables.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from importlib import metadata
from typing import Any

from cyclopts import App

from untaped._root_options import _FLAG_PRESENT as _FLAG_PRESENT
from untaped._root_options import _PROFILE_HELP as _PROFILE_HELP
from untaped._root_options import _QUIET_HELP as _QUIET_HELP
from untaped._root_options import _VERBOSE_HELP as _VERBOSE_HELP
from untaped._root_options import _apply_profile as _apply_profile
from untaped._root_options import _apply_root_option as _apply_root_option
from untaped._root_options import _consume_leading_root_options as _consume_leading_root_options
from untaped._root_options import _consume_option_at as _consume_option_at
from untaped._root_options import _dispatch_with_root_options as _dispatch_with_root_options
from untaped._root_options import _extract_root_option_value as _extract_root_option_value
from untaped._root_options import _match_option as _match_option
from untaped._root_options import _option_names as _option_names
from untaped._root_options import _reset_profile as _reset_profile
from untaped._root_options import _reset_quiet_option as _reset_quiet_option
from untaped._root_options import _reset_verbose_option as _reset_verbose_option
from untaped._root_options import _root_callback_signature as _root_callback_signature
from untaped._root_options import _root_options as _root_options
from untaped._root_options import _RootOption as _RootOption
from untaped._root_options import _strip_trailing_root_option as _strip_trailing_root_option
from untaped._root_options import _unknown_root_option as _unknown_root_option
from untaped.cli import report_errors, run_cyclopts_app
from untaped.config import build_config_app
from untaped.errors import ConfigError
from untaped.profile import build_profile_app
from untaped.skills_app import build_skills_app
from untaped.tool import ToolSpec, register_tool


def build_tool_app(app: App, spec: ToolSpec) -> App:
    """Wire ``spec`` and lazy version lookup onto ``app`` for ``app.meta``."""
    first_wiring = "config" not in app
    register_tool(spec)
    _mount(app, build_config_app(spec), name="config")
    _mount(app, build_profile_app(spec.command), name="profile")
    _mount(app, build_skills_app(spec), name="skills")
    # cyclopts only accepts a name at construction (``App.name`` is a read-only
    # property over the ``_name`` backing field). A tool hands us its own app,
    # so override the backing field to make help/usage read the tool command.
    app._name = (spec.command,)
    distribution = spec.distribution or spec.command

    def resolve_version() -> str:
        try:
            return metadata.version(distribution)
        except metadata.PackageNotFoundError as exc:
            raise ConfigError(
                f"tool {spec.command!r} could not resolve version from "
                f"distribution {distribution!r}"
            ) from exc

    app.version = resolve_version
    if first_wiring:
        # The meta default callback and the completion command can each only be
        # registered once; the mounts above are del-if-present so they re-wire
        # cleanly, but these must be gated to the first wiring.
        _install_root_callback(app, _root_options())
        app.register_install_completion_command()
    return app


def run_tool(
    app: App,
    spec: ToolSpec,
    tokens: Iterable[str] | None = None,
    *,
    console: Any | None = None,
    error_console: Any | None = None,
) -> object:
    """Wire ``spec`` onto ``app`` and run it. Use as a tool's ``main()``."""
    wired = build_tool_app(app, spec)
    return run_cyclopts_app(wired.meta, tokens, console=console, error_console=error_console)


def _mount(app: App, sub: App, *, name: str) -> None:
    """Mount ``sub`` as ``name``, replacing any existing command.

    Makes wiring idempotent so ``build_tool_app`` / ``run_tool`` can be called
    more than once on the same app (tests, embedding) without a collision.
    """
    if name in app:
        del app[name]
    app.command(sub, name=name)


def _install_root_callback(app: App, root_options: dict[str, _RootOption]) -> None:
    # The meta app must not intercept --help/--version: that would render the
    # meta callback instead of the inner app's command listing. The inner app
    # handles both flags after the root options are consumed.
    app.meta.help_flags = ()
    app.meta.version_flags = ()

    def _root_callback(*tokens: str, **_unused: object) -> object:
        # Root-option handlers set invocation-scoped ContextVars (and clear the
        # settings cache). Reset only options this invocation applied so nested
        # in-process callers restore the outer invocation's ContextVars.
        applied_tokens: list[tuple[_RootOption, object]] = []
        try:
            with report_errors():
                command_tokens = _consume_leading_root_options(
                    list(tokens), root_options, applied_tokens
                )
                return _dispatch_with_root_options(
                    app, command_tokens, root_options, applied_tokens
                )
        finally:
            for option, token in reversed(applied_tokens):
                option.resetter(token)

    signature = _root_callback_signature(root_options)
    _root_callback.__signature__ = signature  # type: ignore[attr-defined]
    _root_callback.__annotations__ = {
        parameter.name: parameter.annotation
        for parameter in signature.parameters.values()
        if parameter.annotation is not inspect.Parameter.empty
    }
    app.meta.default(_root_callback)


__all__ = ["build_tool_app", "run_tool"]
