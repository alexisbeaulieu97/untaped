"""Root Cyclopts app for the untaped core and plugin hub."""

from __future__ import annotations

import inspect
import os
from collections.abc import Iterable
from typing import Annotated

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError, UnknownOptionError
from rich.console import Console

from untaped.cli import create_app, echo, raise_usage, report_errors, run_cyclopts_app
from untaped.config import app as config_app
from untaped.environment import environment_diagnostic, startup_mismatch_warning
from untaped.plugin_registry import resolve_lazy_cli, resolve_root_option_handler
from untaped.plugins import (
    PluginRegistry,
    RootOptionSpec,
    UntapedPlugin,
    discover_plugins,
    register_plugins,
    set_current_registry,
)
from untaped.plugins import (
    app as plugins_app,
)
from untaped.settings import get_settings
from untaped.skills import app as skills_app
from untaped.skills import register_builtin_skills

CORE_COMMAND_NAMES = frozenset({"config", "plugins", "skills"})


def build_app(plugins: Iterable[UntapedPlugin] | None = None) -> App:
    """Build a root app and register core commands plus discovered plugins."""
    registry = PluginRegistry(reserved_cli_names=CORE_COMMAND_NAMES)
    register_builtin_skills(registry)
    registry.add_diagnostic("core-environment", environment_diagnostic)
    selected = list(discover_plugins(registry) if plugins is None else plugins)
    register_plugins(registry, selected)
    set_current_registry(registry)
    if plugins is None:
        # Only real entry-point discovery can suffer the foreign-interpreter
        # blind spot; explicit plugin lists (tests, embedding) stay silent.
        warning = startup_mismatch_warning(len(registry.plugin_ids))
        if warning is not None:
            echo(warning, err=True)
    root_options = dict(registry.root_options)

    app = create_app(
        name="untaped",
        help="A personal DevOps CLI suite.",
    )
    # The meta app must not intercept --help/--version: interception happens
    # before the root callback runs _mount_lazy_clis, which would render lazy
    # placeholders instead of the real plugin app for `untaped <cmd> --help`.
    # The inner app handles both flags after mounting.
    app.meta.help_flags = ()
    app.meta.version_flags = ()

    def _root_callback(*tokens: str, **_unused: object) -> object:
        # Root-option handlers mutate ambient process state (env vars, the
        # settings cache) so the dispatched command resolves under the selected
        # scope. Snapshot os.environ and restore it after dispatch so those
        # effects stay scoped to this invocation only — the contract handlers
        # document — for in-process callers (the CLI process exits anyway).
        # report_errors gives handler-raised UntapedErrors the same clean
        # "error: ..." / exit-1 treatment as command bodies.
        env_snapshot = dict(os.environ)
        try:
            with report_errors():
                command_tokens = _consume_leading_root_options(list(tokens), root_options)
                _mount_lazy_clis(app, registry, command_tokens)
                return _dispatch_with_root_options(app, command_tokens, root_options)
        finally:
            if dict(os.environ) != env_snapshot:
                os.environ.clear()
                os.environ.update(env_snapshot)
                get_settings.cache_clear()

    # Cyclopts renders root-option help from the signature; the options are
    # parse=False because consumption stays manual (leading fast path plus
    # strip-on-unknown retry) so passthrough commands keep their own tokens.
    # __signature__ and __annotations__ must agree: cyclopts takes parameter
    # kinds from the former and resolves types through the latter.
    signature = _root_callback_signature(root_options)
    _root_callback.__signature__ = signature  # type: ignore[attr-defined]
    _root_callback.__annotations__ = {
        parameter.name: parameter.annotation
        for parameter in signature.parameters.values()
        if parameter.annotation is not inspect.Parameter.empty
    }
    app.meta.default(_root_callback)

    app.command(config_app, name="config")
    app.command(plugins_app, name="plugins")
    app.command(skills_app, name="skills")
    for name, plugin_app in registry.clis.items():
        app.command(plugin_app, name=name)
    for name, spec in registry.lazy_clis.items():
        # Placeholder so root --help lists the command without importing the
        # plugin CLI module; _mount_lazy_clis swaps in the real app on dispatch.
        app.command(create_app(name=name, help=spec.help), name=name)
    app.register_install_completion_command()
    return app


def main(
    tokens: Iterable[str] | None = None,
    *,
    console: Console | None = None,
    error_console: Console | None = None,
) -> object:
    """Console-script entrypoint that runs the root meta app."""
    return run_cyclopts_app(
        app.meta,
        tokens,
        console=console,
        error_console=error_console,
    )


def _mount_lazy_clis(app: App, registry: PluginRegistry, command_tokens: list[str]) -> None:
    """Swap the dispatched command's help placeholder for the real plugin app.

    Only the targeted command's CLI module is imported; every other lazy
    command stays a placeholder. Help and unknown-command listings already
    work through the placeholders without importing anything.
    """
    if not command_tokens:
        return
    target = command_tokens[0]
    spec = registry.lazy_clis.get(target)
    if spec is None:
        return
    try:
        plugin_app = resolve_lazy_cli(spec)
    except Exception as exc:
        echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    del registry.lazy_clis[target]
    registry.clis[target] = plugin_app
    del app[target]
    app.command(plugin_app, name=target)


def _root_callback_signature(root_options: dict[str, RootOptionSpec]) -> inspect.Signature:
    """Build the meta callback signature advertising every root option in help."""
    parameters = [
        inspect.Parameter(
            "tokens",
            inspect.Parameter.VAR_POSITIONAL,
            annotation=Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
        )
    ]
    for index, option in enumerate(root_options.values()):
        parameters.append(
            inspect.Parameter(
                f"_root_option_{index}",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[
                    str | None,
                    Parameter(name=option.name, help=option.help, parse=False, show=True),
                ],
            )
        )
    return inspect.Signature(parameters)


def _consume_leading_root_options(
    tokens: list[str],
    root_options: dict[str, RootOptionSpec],
) -> list[str]:
    """Apply and strip root options preceding the command, returning the rest."""
    while tokens:
        name = tokens[0].partition("=")[0]
        if name not in root_options:
            break
        value, tokens = _extract_root_option_value(tokens, 0, name)
        _apply_root_option(root_options[name], value)
    return tokens


def _dispatch_with_root_options(
    app: App,
    command_tokens: list[str],
    root_options: dict[str, RootOptionSpec],
) -> object:
    """Dispatch optimistically; on unknown root option, strip, apply, retry.

    Passthrough commands parse successfully (their ``*args`` absorb every
    token), so their ``--profile``-looking tokens are never stolen; commands
    declaring their own homonymous option win for the same reason. Parse
    errors surface before the command body runs, so a retry never repeats
    side effects.
    """
    remaining = list(command_tokens)
    applied: set[str] = set()
    while True:
        try:
            return app(
                remaining,
                exit_on_error=False,
                print_error=False,
                result_action="return_value",
            )
        except UnknownOptionError as exc:
            name = _unknown_root_option(exc, root_options)
            if name is None or name in applied:
                echo(f"error: {exc}", err=True)
                raise SystemExit(2) from exc
            applied.add(name)
            value, remaining = _strip_trailing_root_option(remaining, name)
            _apply_root_option(root_options[name], value)
        except CycloptsError as exc:
            echo(f"error: {exc}", err=True)
            raise SystemExit(2) from exc


def _unknown_root_option(
    exc: UnknownOptionError,
    root_options: dict[str, RootOptionSpec],
) -> str | None:
    """Return the registered root option behind an unknown-option error.

    Encapsulates the cyclopts coupling: ``UnknownOptionError.token`` carries
    the offending CLI token (`--name` keyword or `--name=value` form).
    """
    token = getattr(exc, "token", None)
    keyword = getattr(token, "keyword", None) or getattr(token, "value", "")
    if not isinstance(keyword, str):
        return None
    name = keyword.partition("=")[0]
    return name if name in root_options else None


def _strip_trailing_root_option(tokens: list[str], name: str) -> tuple[str, list[str]]:
    """Remove the last ``name``/``name=value`` occurrence, returning its value."""
    for index in range(len(tokens) - 1, -1, -1):
        token = tokens[index]
        if token == name or token.startswith(f"{name}="):
            return _extract_root_option_value(tokens, index, name)
    raise_usage(f"{name} expects a value")


def _extract_root_option_value(tokens: list[str], index: int, name: str) -> tuple[str, list[str]]:
    """Pull the value for the root option at ``tokens[index]``.

    Handles both ``--name value`` and ``--name=value`` forms and returns the
    value plus the token list with the option (and its value) removed. Raises a
    usage error when the value is missing.
    """
    _, separator, inline = tokens[index].partition("=")
    if separator:
        if not inline:
            raise_usage(f"{name} expects a value")
        return inline, tokens[:index] + tokens[index + 1 :]
    if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
        raise_usage(f"{name} expects a value")
    return tokens[index + 1], tokens[:index] + tokens[index + 2 :]


def _apply_root_option(spec: RootOptionSpec, value: str) -> None:
    resolve_root_option_handler(spec)(value)


app = build_app()


if __name__ == "__main__":
    main()
