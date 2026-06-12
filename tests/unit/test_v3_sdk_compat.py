"""Transitional v3 SDK compat: released plugins must keep loading against v4 core.

The release-smoke job installs the released plugin branches against this
core. Their modules import the v3-era profile SDK surface
(``ProfileOverrideOption``, ``profile_override``, ``DEFAULT_PROFILE``,
``ProfileSource``, the resolver functions, and the ``config_file`` profile
helpers) — the six CLI-contributing plugins at entry-point load time. If any
of those names stop importing, ``discover_plugins`` records a load error,
the plugin never reaches ``registry.plugin_ids``, and
``untaped plugins list`` renders an empty ``plugin_id`` cell.

These shims are deprecated and removal is gated on the plugin-API-v4
rollout completing across the plugin repos.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

from untaped.main import build_app
from untaped.plugin_context import plugin_context
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")

RELEASED_PLUGIN_MODULE = "fake_released_v3_plugin"

# Mirrors the import surface of the released plugin branches (see the
# release-smoke workflow): the profile SDK names are imported when the
# entry-point module loads, before any manifest/register call runs.
RELEASED_PLUGIN_SOURCE = textwrap.dedent(
    '''
    """Released-style plugin: eagerly imports the v3 profile SDK surface."""

    from untaped import (
        DEFAULT_PROFILE,
        ProfileOverrideOption,
        ProfileSource,
        classify_active_profile,
        effective_active_profile_name,
        profile_override,
        resolve_profiles,
    )
    from untaped.api import CliSpec, PluginManifest
    from untaped.config_file import (
        delete_profile,
        get_active_profile_name,
        list_profile_names,
        read_profile,
        rename_profile,
        set_active_profile,
        write_profile,
    )


    class DemoPlugin:
        id = "demo"
        untaped_api_version = 3

        def manifest(self) -> PluginManifest:
            return PluginManifest(
                clis=(
                    CliSpec(
                        name="demo",
                        import_path="fake_released_v3_plugin_cli:app",
                        help="Demo released plugin command.",
                    ),
                ),
            )


    plugin = DemoPlugin()
    '''
)


class _FakeEntryPoint:
    """Stands in for an installed ``untaped.plugins`` entry point."""

    name = "demo"

    def load(self) -> object:
        import importlib

        return importlib.import_module(RELEASED_PLUGIN_MODULE).plugin


@pytest.fixture
def released_plugin_on_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / f"{RELEASED_PLUGIN_MODULE}.py").write_text(RELEASED_PLUGIN_SOURCE)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, RELEASED_PLUGIN_MODULE, raising=False)


def test_released_v3_cli_plugin_keeps_plugin_id_in_plugins_list(
    _isolated_config: Path,
    released_plugin_on_path: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: PR #273 release-smoke printed empty plugin_id cells.

    Entry-point discovery must survive a released plugin importing the v3
    profile SDK names, and ``plugins list`` must match the loaded id to the
    recorded package.
    """
    monkeypatch.setattr(
        "untaped.plugin_registry.entry_points",
        lambda group: [_FakeEntryPoint()],
    )
    _isolated_config.write_text(
        "plugins:\n"
        "  packages:\n"
        "    - spec: untaped-demo @ git+https://example.invalid/untaped-demo.git\n"
        "      editable: false\n"
    )

    app = build_app()  # plugins=None: real entry-point discovery path

    result = CliInvoker().invoke(
        app.meta,
        ["plugins", "list", "--format", "raw", "--columns", "plugin_id"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["demo"]


LAZY_CLI_MODULE = "fake_v3_profile_cli"

# Mirrors released v3 plugin CLI modules (e.g. untaped-awx ``cli/_apply.py``):
# command-local --profile through the untaped.api compat names.
LAZY_CLI_SOURCE = textwrap.dedent(
    """
    import os

    from untaped.api import ProfileOverrideOption, create_app, echo, profile_override

    app = create_app(name="demo", help="Demo v3 plugin command.")


    @app.command(name="where")
    def where(*, profile: ProfileOverrideOption = None) -> None:
        with profile_override(profile):
            echo(os.environ.get("UNTAPED_PROFILE", "<unset>"))
    """
)


def _v3_lazy_plugin() -> object:
    from untaped.api import CliSpec, PluginManifest

    class LazyPlugin:
        id = "demo"
        untaped_api_version = 3

        def manifest(self) -> PluginManifest:
            return PluginManifest(
                clis=(CliSpec(name="demo", import_path=f"{LAZY_CLI_MODULE}:app"),)
            )

    return LazyPlugin()


@pytest.fixture
def lazy_v3_cli_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / f"{LAZY_CLI_MODULE}.py").write_text(LAZY_CLI_SOURCE)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, LAZY_CLI_MODULE, raising=False)


def test_v3_lazy_cli_using_profile_compat_names_dispatches(
    lazy_v3_cli_on_path: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    app = build_app(plugins=[_v3_lazy_plugin()])

    result = CliInvoker().invoke(app.meta, ["demo", "where", "--profile", "stage"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "stage"
    assert "UNTAPED_PROFILE" not in os.environ


def test_plugin_context_still_accepts_read_time_profile_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v3 plugins call ``plugin_context(profile)``; the override must not
    leak into ambient process state once the context is built."""
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)

    ctx = plugin_context("stage")

    assert ctx.settings is not None
    assert "UNTAPED_PROFILE" not in os.environ
