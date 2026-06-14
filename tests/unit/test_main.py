import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import pytest
from cyclopts import Parameter

from untaped import create_app, echo, get_settings
from untaped.main import build_app
from untaped.plugins import PluginRegistry
from untaped.testing import CliInvoker


class _ProfileEnvProbePlugin:
    id = "profile-env-probe"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        probe_app = create_app(name="probe")

        @probe_app.command(name="current")
        def current() -> None:
            echo(os.environ.get("UNTAPED_PROFILE", ""))

        registry.add_cli("probe", probe_app)


class _PassthroughProbePlugin:
    id = "passthrough-probe"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        probe_app = create_app(name="probe")

        @probe_app.command(name="run")
        def run(
            *args: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
        ) -> None:
            echo(
                json.dumps(
                    {
                        "args": list(args),
                        "profile": os.environ.get("UNTAPED_PROFILE"),
                    },
                    sort_keys=True,
                )
            )

        registry.add_cli("probe", probe_app)


@pytest.fixture
def app() -> object:
    return build_app(plugins=[])


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    get_settings.cache_clear()
    yield cfg
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_verbose() -> Iterator[None]:
    from untaped import verbose

    verbose.reset()
    yield
    verbose.reset()


class _VerboseProbePlugin:
    id = "verbose-probe"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        probe_app = create_app(name="probe")

        @probe_app.command(name="verbose")
        def verbose_cmd() -> None:
            from untaped.verbose import is_verbose

            echo("on" if is_verbose() else "off")

        registry.add_cli("probe", probe_app)


def test_help_lists_core_commands_only_without_plugins(app: object) -> None:
    result = CliInvoker().invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "config" in output
    assert "awx" not in output
    assert "workspace" not in output
    assert "github" not in output
    assert "Manage configuration profiles" not in output


def test_root_help_has_no_profile_option_without_plugins(app: object) -> None:
    """Core registers no root ``--profile``; the option is plugin-contributed."""
    result = CliInvoker().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "--profile" not in result.stdout
    assert "UNTAPED_PROFILE" not in result.stdout
    assert "Global options:" not in result.stdout


def test_subcommand_help_has_no_profile_option_without_plugins(app: object) -> None:
    result = CliInvoker().invoke(app, ["config", "--help"])

    assert result.exit_code == 0
    assert "--profile" not in result.stdout
    assert "Global options:" not in result.stdout


def test_help_lists_skills_core_command(app: object) -> None:
    result = CliInvoker().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "skills" in result.stdout


def test_install_completion_help_is_available(app: object) -> None:
    result = CliInvoker().invoke(app, ["--install-completion", "--help"])

    assert result.exit_code == 0, result.output
    assert "Install shell completion" in result.stdout


def test_skills_list_includes_builtin_untaped_skill(app: object) -> None:
    result = CliInvoker().invoke(app, ["skills", "list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert "untaped" in result.stdout.splitlines()


def test_config_subcommand_help(app: object) -> None:
    result = CliInvoker().invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "set" in result.stdout
    assert "unset" in result.stdout


def test_root_parse_errors_exit_2_and_stderr(app: object) -> None:
    result = CliInvoker().invoke(app, ["--bad"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "error: Unknown command" in result.stderr


def test_trailing_profile_flag_is_not_stolen_from_passthrough_command() -> None:
    """With no root ``--profile`` registered, a passthrough command's
    ``--profile``-looking tokens must reach it verbatim."""
    app = build_app(plugins=[_PassthroughProbePlugin()])

    result = CliInvoker().invoke(app, ["probe", "run", "git", "log", "--profile", "stage"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "args": ["git", "log", "--profile", "stage"],
        "profile": None,
    }


class _SettingsReaderProbePlugin:
    """Probe plugin whose read command has NO command-local --profile."""

    id = "settings-reader-probe"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        probe_app = create_app(name="probe")

        @probe_app.command(name="log-level")
        def log_level() -> None:
            echo(get_settings().log_level)

        registry.add_cli("probe", probe_app)


def _tenant_handler_module(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import sys
    import types

    applied: list[str] = []
    module = types.ModuleType("fake_tenant_handler")
    module.apply = applied.append  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_tenant_handler", module)
    return applied


def _tenant_plugin() -> object:
    from untaped.plugins import PluginManifest, RootOptionSpec

    class TenantPlugin:
        id = "tenant"
        untaped_api_version = 4

        def manifest(self) -> PluginManifest:
            return PluginManifest(
                root_options=(
                    RootOptionSpec(
                        name="--tenant",
                        help="Select the tenant for this invocation.",
                        handler_import_path="fake_tenant_handler:apply",
                    ),
                ),
            )

    return TenantPlugin()


def test_plugin_root_option_appears_in_root_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _tenant_handler_module(monkeypatch)
    app = build_app(plugins=[_tenant_plugin()])

    result = CliInvoker().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "--tenant" in result.stdout
    assert "Select the tenant" in result.stdout


def test_plugin_root_option_leading_position_runs_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied = _tenant_handler_module(monkeypatch)
    app = build_app(plugins=[_tenant_plugin(), _ProfileEnvProbePlugin()])

    result = CliInvoker().invoke(app, ["--tenant", "acme", "probe", "current"])

    assert result.exit_code == 0, result.output
    assert applied == ["acme"]


def test_plugin_root_option_trailing_position_runs_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied = _tenant_handler_module(monkeypatch)
    app = build_app(plugins=[_tenant_plugin(), _ProfileEnvProbePlugin()])

    result = CliInvoker().invoke(app, ["probe", "current", "--tenant", "acme"])

    assert result.exit_code == 0, result.output
    assert applied == ["acme"]


def test_root_option_missing_value_is_usage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied = _tenant_handler_module(monkeypatch)
    app = build_app(plugins=[_tenant_plugin(), _ProfileEnvProbePlugin()])

    result = CliInvoker().invoke(app, ["probe", "current", "--tenant"])

    assert result.exit_code == 2
    assert "expects a value" in result.stderr
    assert applied == []


def test_unknown_option_still_fails_after_root_option_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _tenant_handler_module(monkeypatch)
    app = build_app(plugins=[_tenant_plugin(), _ProfileEnvProbePlugin()])

    result = CliInvoker().invoke(app, ["probe", "current", "--nope", "x"])

    assert result.exit_code == 2
    assert "--nope" in result.stderr


def _root_option_plugin(name: str, handler_import_path: str) -> object:
    from untaped.plugins import PluginManifest, RootOptionSpec

    class _RootOptionPlugin:
        id = "root-option-probe"
        untaped_api_version = 4

        def manifest(self) -> PluginManifest:
            return PluginManifest(
                root_options=(
                    RootOptionSpec(
                        name=name,
                        help="Probe root option.",
                        handler_import_path=handler_import_path,
                    ),
                ),
            )

    return _RootOptionPlugin()


def test_root_option_handler_error_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler raising UntapedError exits 1 with 'error: ...', not a traceback."""
    import sys
    import types

    from untaped.errors import ConfigError

    def _apply(value: str) -> None:
        raise ConfigError(f"unknown tenant: {value}")

    module = types.ModuleType("fake_failing_handler")
    module.apply = _apply  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_failing_handler", module)

    app = build_app(
        plugins=[
            _root_option_plugin("--tenant", "fake_failing_handler:apply"),
            _ProfileEnvProbePlugin(),
        ]
    )

    result = CliInvoker().invoke(app, ["--tenant", "ghost", "probe", "current"])

    assert result.exit_code == 1
    assert "error: unknown tenant: ghost" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_root_option_env_side_effect_does_not_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler's env mutation is reverted after the invocation (in-process)."""
    import sys
    import types

    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)

    def _apply(value: str) -> None:
        os.environ["UNTAPED_PROFILE"] = value

    module = types.ModuleType("fake_env_handler")
    module.apply = _apply  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_env_handler", module)

    app = build_app(
        plugins=[
            _root_option_plugin("--profile", "fake_env_handler:apply"),
            _ProfileEnvProbePlugin(),
        ]
    )

    first = CliInvoker().invoke(app, ["--profile", "acme", "probe", "current"])
    assert first.exit_code == 0, first.output
    assert first.stdout.strip() == "acme"
    assert "UNTAPED_PROFILE" not in os.environ

    second = CliInvoker().invoke(app, ["probe", "current"])
    assert second.exit_code == 0, second.output
    assert second.stdout.strip() == ""


def test_plugin_settings_layout_drives_settings_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A v4 plugin layout makes flat top-level config keys effective."""
    import sys
    import types

    from untaped.plugins import PluginManifest, SettingsLayoutSpec
    from untaped.settings import reset_config_registry_for_tests
    from untaped.settings_layout import FlatSettingsLayout

    module = types.ModuleType("fake_layout_module")
    module.LAYOUT = FlatSettingsLayout()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_layout_module", module)

    class FlatLayoutPlugin:
        id = "flat-layout"
        untaped_api_version = 4

        def manifest(self) -> PluginManifest:
            return PluginManifest(
                settings_layout=SettingsLayoutSpec(import_path="fake_layout_module:LAYOUT"),
            )

    cfg = tmp_path / "config.yml"
    cfg.write_text("log_level: DEBUG\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    reset_config_registry_for_tests()
    try:
        app = build_app(plugins=[FlatLayoutPlugin(), _SettingsReaderProbePlugin()])
        result = CliInvoker().invoke(app, ["probe", "log-level"])

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == ["DEBUG"]
    finally:
        reset_config_registry_for_tests()


def test_flat_top_level_settings_reach_plugin_commands(
    _isolate_config: Path,
) -> None:
    """Default flat layout: top-level config keys are the effective settings."""
    _isolate_config.write_text("log_level: DEBUG\n")
    app = build_app(plugins=[_SettingsReaderProbePlugin()])

    result = CliInvoker().invoke(app, ["probe", "log-level"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["DEBUG"]


def test_trailing_profile_token_is_unknown_option_without_plugins(
    _isolate_config: Path,
) -> None:
    """With no root ``--profile`` registered there is no strip-on-retry
    fallback for it — the token is a plain unknown option (exit 2)."""
    app = build_app(plugins=[_SettingsReaderProbePlugin()])

    result = CliInvoker().invoke(app, ["probe", "log-level", "--profile", "stage"])

    assert result.exit_code == 2
    assert "--profile" in result.stderr


def test_build_app_registers_environment_diagnostic() -> None:
    registry_holder: list[PluginRegistry] = []

    class _RegistryCapturePlugin:
        id = "registry-capture"
        untaped_api_version = 2

        def register(self, registry: PluginRegistry) -> None:
            registry_holder.append(registry)

    build_app(plugins=[_RegistryCapturePlugin()])

    # register() runs on a staging copy; the diagnostic must survive adoption.
    assert "core-environment" in registry_holder[0].diagnostics


def test_explicit_plugins_skip_environment_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _fail(loaded_plugin_count: int) -> str | None:
        raise AssertionError("warning must not be computed for explicit plugins")

    monkeypatch.setattr("untaped.main.startup_mismatch_warning", _fail)
    build_app(plugins=[])
    assert capsys.readouterr().err == ""


def test_discovery_emits_environment_warning_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("untaped.main.discover_plugins", lambda registry: [])
    monkeypatch.setattr(
        "untaped.main.startup_mismatch_warning",
        lambda loaded_plugin_count: "warning: recorded plugins cannot load",
    )
    build_app(plugins=None)
    captured = capsys.readouterr()
    assert "warning: recorded plugins cannot load" in captured.err
    assert captured.out == ""


def test_core_verbose_flag_appears_in_root_help(app: object) -> None:
    result = CliInvoker().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "--verbose" in result.stdout


def test_verbose_flag_leading_position_enables_verbose() -> None:
    app = build_app(plugins=[_VerboseProbePlugin()])

    result = CliInvoker().invoke(app, ["--verbose", "probe", "verbose"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "on"


def test_verbose_short_alias_trailing_position_enables_verbose() -> None:
    app = build_app(plugins=[_VerboseProbePlugin()])

    result = CliInvoker().invoke(app, ["probe", "verbose", "-v"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "on"


def test_without_verbose_flag_stays_off() -> None:
    app = build_app(plugins=[_VerboseProbePlugin()])

    result = CliInvoker().invoke(app, ["probe", "verbose"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "off"


def test_verbose_flag_is_reset_after_invocation() -> None:
    from untaped.verbose import is_verbose

    app = build_app(plugins=[_VerboseProbePlugin()])

    CliInvoker().invoke(app, ["--verbose", "probe", "verbose"])

    assert is_verbose() is False
