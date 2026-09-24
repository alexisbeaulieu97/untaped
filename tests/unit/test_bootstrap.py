"""Tests for the unified capability composition root.

Discovery and validation happen before settings registration or app mounting;
the root also keeps invocation-scoped option and reset behavior.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

import pytest
from cyclopts import App
from pydantic import BaseModel

from untaped import bootstrap
from untaped.app_context import app_context
from untaped.capabilities.registry import CapabilitySpec, ExternalProvider
from untaped.cli import create_app, echo
from untaped.errors import ConfigError
from untaped.profile_resolver import profile_override, set_profile_override
from untaped.quiet import is_quiet
from untaped.settings import get_settings, get_settings_model, reset_config_registry_for_tests
from untaped.testing import CliInvoker
from untaped.verbose import is_verbose


class _ExtProfile(BaseModel):
    token: str = "default-token"


@pytest.fixture(autouse=True)
def _bootstrap_isolation() -> None:
    bootstrap._clear_for_tests()
    yield
    bootstrap._clear_for_tests()


class _Provider:
    """Nullary external provider double recording its invocations."""

    def __init__(self, spec: CapabilitySpec, calls: list[str]) -> None:
        self.api_requires = (1.0, 2.0)
        self._spec = spec
        self._calls = calls

    def __call__(self) -> CapabilitySpec:
        self._calls.append(self._spec.name)
        return self._spec


def _external(
    spec: CapabilitySpec, calls: list[str], *, distribution: str = "example-dist"
) -> ExternalProvider:
    return ExternalProvider(
        distribution=distribution, name=spec.name, target=_Provider(spec, calls)
    )


def _spec(name: str, app: App) -> CapabilitySpec:
    def _factory() -> App:
        return app

    return CapabilitySpec(
        name=name,
        app_factory=_factory,
        config_section=name,
        profile_model=_ExtProfile,
    )


def _token_body_for(section: str) -> Callable[[], None]:
    def _body() -> None:
        echo(app_context().section(section, _ExtProfile).token)

    return _body


def _who_app(name: str, body: Callable[[], None]) -> App:
    app = create_app(name=name, help=f"{name} capability.")
    app.command(body, name="who")
    return app


def _ext_external(calls: list[str]) -> ExternalProvider:
    return _external(_spec("ext", _who_app("ext", _token_body_for("ext"))), calls)


def _write_config(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()


def test_zero_capability_root_lists_no_capabilities() -> None:
    composition = bootstrap.compose_root(builtins=(), externals=())
    assert composition.capabilities == ()
    assert composition.quarantine == ()

    root = bootstrap.build_root_app(builtins=(), externals=())
    assert bootstrap.current_capability() is None
    result = CliInvoker().invoke(root.meta, ["--help"])
    assert result.exit_code == 0, result.output
    assert "untaped" in result.stdout
    assert bootstrap.current_capability() is None


def test_default_composition_retains_the_six_public_capabilities() -> None:
    expected = ("workspace", "github", "jira", "awx", "ansible", "recipe")

    composition = bootstrap.compose_root()

    assert tuple(capability.spec.name for capability in composition.capabilities) == expected
    assert composition.quarantine == ()

    root = bootstrap.build_root_app()
    for name in expected:
        result = CliInvoker().invoke(root.meta, [name, "--help"])
        assert result.exit_code == 0, result.output


def test_retired_orchestration_command_is_unknown() -> None:
    root = bootstrap.build_root_app()

    result = CliInvoker().invoke(root.meta, ["orchestration"])

    assert result.exit_code == 2
    assert "orchestration" in result.output


def test_retired_orchestration_capability_is_absent() -> None:
    capability_path = (
        Path(__file__).resolve().parents[2] / "src" / "untaped" / "capabilities" / "orchestration"
    )

    assert not capability_path.exists()
    assert importlib.util.find_spec("untaped.capabilities.orchestration") is None


def test_retired_orchestration_config_schema_is_absent() -> None:
    root = bootstrap.build_root_app()

    assert "orchestration" not in get_settings_model().model_fields

    result = CliInvoker().invoke(
        root.meta,
        ["config", "list", "--format", "raw", "--columns", "key"],
    )
    assert result.exit_code == 0, result.output
    assert not any(line.startswith("orchestration.") for line in result.stdout.splitlines())


def test_retired_orchestration_packaged_skill_is_absent() -> None:
    root = bootstrap.build_root_app()

    result = CliInvoker().invoke(
        root.meta,
        ["skills", "list", "--format", "raw", "--columns", "name"],
    )

    assert result.exit_code == 0, result.output
    assert "untaped-orchestration" not in result.stdout


def test_version_resolves_unified_distribution_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    looked_up: list[str] = []

    def fake_version(distribution: str) -> str:
        looked_up.append(distribution)
        return "9.8.7"

    monkeypatch.setattr(metadata, "version", fake_version)
    calls: list[str] = []
    root = bootstrap.build_root_app(builtins=(), externals=[_ext_external(calls)])
    assert looked_up == []

    result = CliInvoker().invoke(root.meta, ["ext", "who"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "default-token"
    assert looked_up == []

    result = CliInvoker().invoke(root.meta, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "9.8.7\n"
    assert result.stderr == ""
    assert looked_up == ["untaped"]


def test_missing_version_metadata_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(distribution: str) -> str:
        raise metadata.PackageNotFoundError(distribution)

    monkeypatch.setattr(metadata, "version", missing)
    root = bootstrap.build_root_app(builtins=(), externals=())
    result = CliInvoker().invoke(root.meta, ["--version"])
    assert result.exit_code == 1, result.output
    assert "untaped" in result.stderr
    assert isinstance(result.exception, SystemExit)


def test_discovery_runs_before_settings_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    calls: list[str] = []
    candidate = _external(_spec("ext", _who_app("ext", _token_body_for("ext"))), calls)

    def fake_discover(**kwargs: object) -> tuple[ExternalProvider, ...]:
        events.append("discover")
        return (candidate,)

    real_register = bootstrap.register_profile_settings

    def spy_register(section: str, model: object) -> None:
        events.append(f"register:{section}")
        real_register(section, model)  # type: ignore[arg-type]

    monkeypatch.setattr(bootstrap, "discover_external_providers", fake_discover)
    monkeypatch.setattr(bootstrap, "register_profile_settings", spy_register)
    root = bootstrap.build_root_app(builtins=(), externals=None)

    assert "ext" in root
    assert events[0] == "discover"
    assert events.index("register:shell") < events.index("register:ext")


def test_external_settings_register_before_config_resolution(_isolated_config: Path) -> None:
    _write_config(_isolated_config, "profiles:\n  default:\n    ext:\n      token: SECRET\n")
    with pytest.raises(ConfigError):
        app_context().section("ext", _ExtProfile)

    calls: list[str] = []
    root = bootstrap.build_root_app(builtins=(), externals=[_ext_external(calls)])
    assert calls == ["ext"]
    assert "ext" in root
    assert app_context().section("ext", _ExtProfile).token == "SECRET"
    assert get_settings().ext.token == "SECRET"  # type: ignore[attr-defined]

    result = CliInvoker().invoke(root.meta, ["ext", "who"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "SECRET"


def test_profile_option_resolves_in_any_position(_isolated_config: Path) -> None:
    _write_config(_isolated_config, "profiles:\n  work:\n    ext:\n      token: WT\n")
    calls: list[str] = []
    root = bootstrap.build_root_app(builtins=(), externals=[_ext_external(calls)])
    for argv in (
        ["--profile", "work", "ext", "who"],
        ["ext", "--profile", "work", "who"],
        ["ext", "--profile=work", "who"],
        ["ext", "who", "--profile", "work"],
        ["--profile", "work", "--", "ext", "who"],
    ):
        result = CliInvoker().invoke(root.meta, argv)
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == "WT"
    assert profile_override() is None


@pytest.mark.parametrize(
    "argv",
    [
        ["-q", "--profile", "work", "ext", "grp", "who"],
        ["ext", "-q", "--profile", "work", "grp", "who"],
        ["ext", "grp", "--quiet", "--profile", "work", "who"],
        ["ext", "--profile", "work", "grp", "who", "-q"],
    ],
)
def test_root_options_apply_between_nested_command_names(
    _isolated_config: Path, argv: list[str]
) -> None:
    _write_config(_isolated_config, "profiles:\n  work:\n    ext:\n      token: WT\n")

    def body() -> None:
        echo(f"{app_context().section('ext', _ExtProfile).token} quiet={is_quiet()}")

    ext = create_app(name="ext", help="ext capability.")
    grp = create_app(name="grp", help="A nested group.")
    grp.command(body, name="who")
    ext.command(grp, name="grp")
    root = bootstrap.build_root_app(builtins=(), externals=[_external(_spec("ext", ext), [])])

    result = CliInvoker().invoke(root.meta, argv)

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "WT quiet=True"
    assert profile_override() is None
    assert not is_quiet()


def test_root_option_after_a_lazy_builtin_name_is_not_a_command(
    _isolated_config: Path,
) -> None:
    root = bootstrap.build_root_app()

    result = CliInvoker().invoke(root.meta, ["workspace", "--profile", "nope", "list"])

    assert result.exit_code == 1
    assert "Unknown command" not in result.stderr
    assert "'nope'" in result.stderr


def test_profile_value_may_equal_a_command_name(_isolated_config: Path) -> None:
    _write_config(_isolated_config, "profiles:\n  ext:\n    ext:\n      token: EXT\n")
    root = bootstrap.build_root_app(builtins=(), externals=[_ext_external([])])
    for argv in (
        ["--profile", "ext", "ext", "who"],
        ["ext", "--profile", "ext", "who"],
        ["ext", "who", "--profile=ext"],
    ):
        result = CliInvoker().invoke(root.meta, argv)
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == "EXT"


def test_root_options_after_end_of_options_reach_the_command(
    _isolated_config: Path,
) -> None:
    def run(cmd: str, /) -> None:
        echo(f"{cmd} profile={profile_override()}")

    ext = create_app(name="ext", help="ext capability.")
    ext.command(run, name="run")
    root = bootstrap.build_root_app(builtins=(), externals=[_external(_spec("ext", ext), [])])

    result = CliInvoker().invoke(root.meta, ["ext", "run", "--", "--profile"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "--profile profile=None"

    # Extra positionals after ``--`` are a usage error, never a root option.
    result = CliInvoker().invoke(root.meta, ["ext", "run", "--", "echo", "--profile", "x"])
    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "expects a value" not in result.stderr


def test_root_options_reset_after_invocation(_isolated_config: Path) -> None:
    _write_config(_isolated_config, "profiles:\n  work:\n    ext:\n      token: WT\n")
    env_before = os.environ.get("UNTAPED_PROFILE")
    calls: list[str] = []
    root = bootstrap.build_root_app(builtins=(), externals=[_ext_external(calls)])

    result = CliInvoker().invoke(root.meta, ["--verbose", "ext", "who"])
    assert result.exit_code == 0, result.output
    assert not is_verbose()
    assert not is_quiet()

    result = CliInvoker().invoke(root.meta, ["ext", "who", "--quiet"])
    assert result.exit_code == 0, result.output
    assert not is_verbose()
    assert not is_quiet()

    result = CliInvoker().invoke(root.meta, ["--profile", "work", "ext", "who"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "WT"
    assert profile_override() is None
    assert os.environ.get("UNTAPED_PROFILE") == env_before


@pytest.mark.parametrize(
    "argv",
    [
        ["-v", "-q", "ext", "who"],
        ["--quiet", "ext", "who", "--verbose"],
        ["ext", "who", "-q", "-v"],
    ],
)
def test_verbose_and_quiet_together_is_a_usage_error(
    _isolated_config: Path, argv: list[str]
) -> None:
    calls: list[str] = []
    root = bootstrap.build_root_app(builtins=(), externals=[_ext_external(calls)])

    result = CliInvoker().invoke(root.meta, argv)

    assert result.exit_code == 2
    assert "--verbose and --quiet cannot be combined" in result.stderr
    assert result.stdout == ""
    assert not is_verbose()
    assert not is_quiet()


def test_identity_resets_after_nested_calls() -> None:
    seen: dict[str, object] = {}
    holder: dict[str, object] = {}

    def beta_body() -> None:
        echo(f"inner:{bootstrap.current_capability()}")

    def alpha_body() -> None:
        seen["before"] = bootstrap.current_capability()
        nested = CliInvoker().invoke(holder["root"], ["beta", "who"])  # type: ignore[arg-type]
        seen["nested_exit"] = nested.exit_code
        seen["nested_out"] = nested.stdout.strip()
        echo(f"outer:{bootstrap.current_capability()}")

    alpha_calls: list[str] = []
    beta_calls: list[str] = []
    root = bootstrap.build_root_app(
        builtins=(),
        externals=[
            _external(_spec("alpha", _who_app("alpha", alpha_body)), alpha_calls),
            _external(_spec("beta", _who_app("beta", beta_body)), beta_calls),
        ],
    )
    holder["root"] = root.meta

    assert bootstrap.current_capability() is None
    result = CliInvoker().invoke(root.meta, ["alpha", "who"])
    assert result.exit_code == 0, result.output
    assert seen == {"before": "alpha", "nested_exit": 0, "nested_out": "inner:beta"}
    assert "outer:alpha" in result.stdout
    assert bootstrap.current_capability() is None


def test_root_help_lists_root_options_and_completion() -> None:
    root = bootstrap.build_root_app(builtins=(), externals=())
    result = CliInvoker().invoke(root.meta, ["--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--profile", "--verbose", "--quiet", "--install-completion"):
        assert flag in result.stdout


def test_bootstrap_has_no_standalone_composition_imports() -> None:
    src_dir = Path(bootstrap.__file__).resolve().parent
    for filename in ("bootstrap.py", "__main__.py"):
        tree = ast.parse((src_dir / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {"untaped.run", "untaped.tool"}, filename
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in {"untaped.run", "untaped.tool"}, filename
            elif isinstance(node, ast.Name):
                assert node.id not in {
                    "ToolSpec",
                    "register_tool",
                    "build_tool_app",
                    "run_tool",
                }, filename


def test_quarantined_external_warns_and_boots(capsys: pytest.CaptureFixture[str]) -> None:
    good_calls: list[str] = []
    bad_calls: list[str] = []
    good = _external(_spec("good", _who_app("good", _token_body_for("good"))), good_calls)
    bad_spec = CapabilitySpec(
        name="good",
        app_factory=lambda: create_app(name="bad", help="bad capability."),
        config_section="other",
        profile_model=_ExtProfile,
    )
    bad = _external(bad_spec, bad_calls)

    composition = bootstrap.compose_root(builtins=(), externals=[good, bad])
    assert [cap.spec.name for cap in composition.capabilities] == ["good"]
    assert len(composition.quarantine) == 1
    assert composition.quarantine[0].reason == "duplicate-name"
    err = capsys.readouterr().err
    assert "quarantined" in err
    assert "example-dist" in err

    root = bootstrap.build_root_app(builtins=(), externals=[good, bad])
    capsys.readouterr()
    assert "good" in root
    result = CliInvoker().invoke(root.meta, ["good", "who"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "default-token"


def test_reset_restores_composed_state(_isolated_config: Path) -> None:
    calls: list[str] = []
    bootstrap.build_root_app(builtins=(), externals=[_ext_external(calls)])
    assert app_context().section("ext", _ExtProfile).token == "default-token"

    set_profile_override("work")
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    with pytest.raises(ConfigError):
        app_context().section("ext", _ExtProfile)

    bootstrap.reset()
    assert profile_override() is None
    assert bootstrap.current_capability() is None
    assert app_context().section("ext", _ExtProfile).token == "default-token"
    assert not is_verbose()
    assert not is_quiet()


def test_module_entrypoint_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "untaped", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "untaped" in proc.stdout


def test_installed_wheel_reports_version_and_help(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the installed-wheel smoke test")
    repo = Path(__file__).resolve().parents[2]
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    built = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stderr
    wheels = sorted(dist_dir.glob("untaped-*-py3-none-any.whl"))
    assert len(wheels) == 1
    assert wheels[0].name == "untaped-6.0.1-py3-none-any.whl"

    venv_dir = tmp_path / "smoke-venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, timeout=300)
    venv_python = venv_dir / "bin" / "python"
    installed = subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            str(venv_python),
            str(wheels[0]),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert installed.returncode == 0, installed.stderr
    assert (venv_dir / "bin" / "untaped").is_file()

    link_dir = tmp_path / "deps"
    link_dir.mkdir()
    purelib = Path(sysconfig.get_paths()["purelib"])
    for entry in purelib.iterdir():
        if (
            entry.name.startswith("untaped")
            or entry.name.startswith("__editable__")
            or entry.name.endswith(".pth")
            or entry.name == "__pycache__"
        ):
            continue
        os.symlink(entry, link_dir / entry.name, target_is_directory=entry.is_dir())
    env = dict(
        os.environ,
        PYTHONPATH=str(link_dir),
        UNTAPED_CONFIG=str(tmp_path / "smoke-config.yml"),
    )
    version = subprocess.run(
        [str(venv_dir / "bin" / "untaped"), "--version"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout == "6.0.1\n"

    helped = subprocess.run(
        [str(venv_dir / "bin" / "untaped"), "--help"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert helped.returncode == 0, helped.stderr
    assert "untaped" in helped.stdout


def _counting_spec(
    name: str, calls: list[str], *, help: str | None = None, result: object = None
) -> CapabilitySpec:
    def _factory() -> App:
        calls.append(name)
        if result is not None:
            return result  # type: ignore[return-value]
        return _who_app(name, _token_body_for(name))

    return CapabilitySpec(
        name=name,
        app_factory=_factory,
        config_section=name,
        profile_model=_ExtProfile,
        help=help,
    )


def test_lazy_builtin_factory_runs_only_on_dispatch_and_once() -> None:
    calls: list[str] = []
    spec = _counting_spec("lazy", calls, help="Lazy capability.")
    root = bootstrap.build_root_app(builtins=(spec,), externals=())
    assert calls == []

    listed = CliInvoker().invoke(root.meta, ["--help"])
    assert listed.exit_code == 0, listed.output
    assert "Lazy capability." in listed.stdout
    assert calls == []

    for _ in range(2):
        result = CliInvoker().invoke(root.meta, ["lazy", "who"])
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == "default-token"
    assert calls == ["lazy"]


def test_eager_factories_are_called_once_per_composition() -> None:
    builtin_calls: list[str] = []
    external_calls: list[str] = []
    builtin = _counting_spec("eager", builtin_calls)
    external = _external(_counting_spec("ext", external_calls), [])

    root = bootstrap.build_root_app(builtins=(builtin,), externals=[external])
    for name in ("eager", "ext"):
        result = CliInvoker().invoke(root.meta, [name, "who"])
        assert result.exit_code == 0, result.output

    assert builtin_calls == ["eager"]
    assert external_calls == ["ext"]


def test_external_help_does_not_defer_factory_validation() -> None:
    calls: list[str] = []
    bad = _counting_spec("bad", calls, help="Bad capability.", result="not-an-app")
    composition = bootstrap.compose_root(builtins=(), externals=[_external(bad, [])])
    assert composition.capabilities == ()
    assert [record.reason for record in composition.quarantine] == ["bad-app-factory"]
    assert calls == ["bad"]


def test_lazy_builtin_bad_factory_is_fatal_at_dispatch() -> None:
    calls: list[str] = []
    bad = _counting_spec("bad", calls, help="Bad capability.", result="not-an-app")
    root = bootstrap.build_root_app(builtins=(bad,), externals=())
    assert calls == []

    result = CliInvoker().invoke(root.meta, ["bad", "who"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)
    assert "bad-app-factory" in str(result.exception)


def test_lazy_builtin_raising_factory_is_fatal_at_dispatch() -> None:
    def _boom() -> App:
        raise RuntimeError("factory-boom")

    spec = CapabilitySpec(
        name="boom",
        app_factory=_boom,
        config_section="boom",
        profile_model=_ExtProfile,
        help="Boom capability.",
    )
    root = bootstrap.build_root_app(builtins=(spec,), externals=())

    result = CliInvoker().invoke(root.meta, ["boom", "--help"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)
    assert "bad-app-factory" in str(result.exception)
    assert "factory-boom" in str(result.exception)


#: Private cyclopts internals ``_LazyCapabilityCommand`` relies on. Drift here
#: (a cyclopts upgrade within ``>=4.16,<5``) must fail loudly, not render oddly.
_CYCLOPTS_PRIVATE_INTERNALS = (
    "cyclopts.core._apply_parent_defaults_to_app",
    "cyclopts.core.App._commands",
    "cyclopts.core.App._name_transform",
    "cyclopts.command_spec.CommandSpec._resolved",
)


def test_cyclopts_private_internals_used_by_lazy_mounts_exist() -> None:
    import cyclopts.command_spec
    import cyclopts.core

    app = App(name="probe")
    spec = cyclopts.command_spec.CommandSpec(import_path="probe:app", name="probe")
    missing = [
        path
        for path, present in zip(
            _CYCLOPTS_PRIVATE_INTERNALS,
            (
                callable(getattr(cyclopts.core, "_apply_parent_defaults_to_app", None)),
                isinstance(getattr(app, "_commands", None), dict),
                hasattr(app, "_name_transform"),
                hasattr(spec, "_resolved"),
            ),
            strict=True,
        )
        if not present
    ]
    assert not missing, (
        "cyclopts internal API drift: bootstrap._LazyCapabilityCommand relies on "
        f"{', '.join(missing)}; update it (or pin cyclopts) before upgrading"
    )


def test_lazy_builtins_render_like_eager_mounts() -> None:
    from dataclasses import replace

    eager_specs = tuple(replace(spec, help=None) for spec in bootstrap.BUILTIN_CAPABILITIES)
    argv_cases = [["--help"]] + [
        [spec.name, flag]
        for spec in bootstrap.BUILTIN_CAPABILITIES
        for flag in ("--help", "--version")
    ]
    for argv in argv_cases:
        lazy = CliInvoker().invoke(bootstrap.build_root_app(externals=()).meta, argv)
        eager = CliInvoker().invoke(
            bootstrap.build_root_app(builtins=eager_specs, externals=()).meta, argv
        )
        assert (lazy.exit_code, lazy.output) == (eager.exit_code, eager.output), (
            f"lazy mount of {argv} renders differently from an eager mount; cyclopts "
            "internals used by bootstrap._LazyCapabilityCommand may have drifted: "
            f"{', '.join(_CYCLOPTS_PRIVATE_INTERNALS)}"
        )


def test_builtin_help_matches_app_summary() -> None:
    for spec in bootstrap.BUILTIN_CAPABILITIES:
        assert spec.help is not None, spec.name
        assert spec.help == spec.app_factory().help, spec.name
