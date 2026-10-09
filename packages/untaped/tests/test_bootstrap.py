"""Tests for the unified plugin composition root.

Discovery and validation happen before settings registration or app mounting;
the root also keeps invocation-scoped option and reset behavior.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tomllib
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

import pytest
from cyclopts import App
from pydantic import BaseModel

from test_plugins.plugin_harness import make_candidate, make_spec
from untaped import bootstrap
from untaped.app_context import app_context
from untaped.cli import create_app, echo
from untaped.errors import ConfigError
from untaped.plugins.registry import PluginSpec, ProviderCandidate
from untaped.profile_resolver import profile_override, set_profile_override
from untaped.quiet import is_quiet
from untaped.settings import get_settings, reset_config_registry_for_tests
from untaped.testing import CliInvoker, provider_candidate
from untaped.verbose import is_verbose


class _ExtProfile(BaseModel):
    token: str = "default-token"


pytestmark = pytest.mark.usefixtures("fresh_composition")


def _spec(name: str, app: App) -> PluginSpec:
    def _factory() -> App:
        return app

    return PluginSpec(
        name=name,
        app_factory=_factory,
        settings=_ExtProfile,
    )


def _token_body_for(section: str) -> Callable[[], None]:
    def _body() -> None:
        echo(app_context().section(section, _ExtProfile).token)

    return _body


def _who_app(name: str, body: Callable[[], None]) -> App:
    app = create_app(name=name, help=f"{name} plugin.")
    app.command(body, name="who")
    return app


def _ext_candidate(calls: list[str]) -> ProviderCandidate:
    return make_candidate(_spec("ext", _who_app("ext", _token_body_for("ext"))), calls=calls)


def _write_config(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()


def test_zero_plugin_root_lists_no_plugins() -> None:
    composition = bootstrap.compose_root(candidates=())
    assert composition.plugins == ()
    assert composition.quarantine == ()

    root = bootstrap.build_root_app(candidates=())
    result = CliInvoker().invoke(root.meta, ["--help"])
    assert result.exit_code == 0, result.output
    assert "untaped" in result.stdout


def test_composition_is_the_last_composed_result() -> None:
    with pytest.raises(RuntimeError):
        bootstrap.composition()
    composed = bootstrap.compose_root(candidates=())
    assert bootstrap.composition() is composed


def test_version_resolves_unified_distribution_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    looked_up: list[str] = []

    def fake_version(distribution: str) -> str:
        looked_up.append(distribution)
        return "9.8.7"

    monkeypatch.setattr(metadata, "version", fake_version)
    calls: list[str] = []
    root = bootstrap.build_root_app(candidates=[_ext_candidate(calls)])
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
    root = bootstrap.build_root_app(candidates=())
    result = CliInvoker().invoke(root.meta, ["--version"])
    assert result.exit_code == 4, result.output  # config: the environment needs fixing
    assert "untaped" in result.stderr
    assert isinstance(result.exception, SystemExit)


def test_discovery_runs_before_settings_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    calls: list[str] = []
    candidate = make_candidate(_spec("ext", _who_app("ext", _token_body_for("ext"))), calls=calls)

    def fake_discover(**kwargs: object) -> tuple[ProviderCandidate, ...]:
        events.append("discover")
        return (candidate,)

    real_register = bootstrap.register_profile_settings

    def spy_register(section: str, model: object, stability: object = None) -> None:
        events.append(f"register:{section}")
        real_register(section, model, stability)  # type: ignore[arg-type]

    monkeypatch.setattr(bootstrap, "discover_candidates", fake_discover)
    monkeypatch.setattr(bootstrap, "register_profile_settings", spy_register)
    root = bootstrap.build_root_app(candidates=None)

    assert "ext" in root
    assert events[0] == "discover"
    assert events.index("register:shell") < events.index("register:ext")


def test_provider_settings_register_before_config_resolution(_isolated_config: Path) -> None:
    _write_config(_isolated_config, "profiles:\n  default:\n    ext:\n      token: SECRET\n")
    with pytest.raises(ConfigError):
        app_context().section("ext", _ExtProfile)

    calls: list[str] = []
    root = bootstrap.build_root_app(candidates=[_ext_candidate(calls)])
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
    root = bootstrap.build_root_app(candidates=[_ext_candidate(calls)])
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

    ext = create_app(name="ext", help="ext plugin.")
    grp = create_app(name="grp", help="A nested group.")
    grp.command(body, name="who")
    ext.command(grp, name="grp")
    root = bootstrap.build_root_app(candidates=[make_candidate(_spec("ext", ext))])

    result = CliInvoker().invoke(root.meta, argv)

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "WT quiet=True"
    assert profile_override() is None
    assert not is_quiet()


def test_profile_value_may_equal_a_command_name(_isolated_config: Path) -> None:
    _write_config(_isolated_config, "profiles:\n  ext:\n    ext:\n      token: EXT\n")
    root = bootstrap.build_root_app(candidates=[_ext_candidate([])])
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

    ext = create_app(name="ext", help="ext plugin.")
    ext.command(run, name="run")
    root = bootstrap.build_root_app(candidates=[make_candidate(_spec("ext", ext))])

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
    root = bootstrap.build_root_app(candidates=[_ext_candidate(calls)])

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
    root = bootstrap.build_root_app(candidates=[_ext_candidate(calls)])

    result = CliInvoker().invoke(root.meta, argv)

    assert result.exit_code == 2
    assert "--verbose and --quiet cannot be combined" in result.stderr
    assert result.stdout == ""
    assert not is_verbose()
    assert not is_quiet()


def test_root_help_lists_root_options_and_completion() -> None:
    root = bootstrap.build_root_app(candidates=())
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


def test_quarantined_providers_warn_and_the_root_boots(
    capsys: pytest.CaptureFixture[str],
) -> None:
    good = make_candidate(_spec("good", _who_app("good", _token_body_for("good"))))
    built: list[str] = []

    def rival_factory() -> App:
        built.append("rival")
        return create_app(name="rival", help="rival plugin.")

    # Two providers claiming one name are both quarantined; neither is built.
    rivals = [
        make_candidate(
            PluginSpec(
                name="rival",
                app_factory=rival_factory,
                settings=_ExtProfile,
            ),
            distribution,
        )
        for section, distribution in (("rival", "example-dist"), ("rival2", "acme-dist"))
    ]
    # A first-party-style provider gets no special treatment when it raises.
    raising = make_candidate(
        _spec("awx", App(name="awx")), "untaped", error=ImportError("settings module is broken")
    )
    candidates = [good, *rivals, raising]

    composition = bootstrap.compose_root(candidates=candidates)
    assert [cap.spec.name for cap in composition.plugins] == ["good"]
    assert [(r.name, r.distribution, r.reason) for r in composition.quarantine] == [
        ("awx", "untaped", "malformed-entry-point"),
        ("rival", "acme-dist", "duplicate-name"),
        ("rival", "example-dist", "duplicate-name"),
    ]
    assert built == []
    for section in ("rival", "rival2"):
        with pytest.raises(ConfigError):
            app_context().section(section, _ExtProfile)
    err = capsys.readouterr().err
    assert "'awx' from 'untaped' quarantined [malformed-entry-point]" in err
    assert (
        "'rival' from 'acme-dist' quarantined [duplicate-name]: duplicate plugin "
        "name: 'rival' (claimed by 'acme-dist', 'example-dist')"
    ) in err

    root = bootstrap.build_root_app(candidates=candidates)
    capsys.readouterr()
    assert "good" in root
    assert "awx" not in root
    assert "rival" not in root
    assert built == []
    result = CliInvoker().invoke(root.meta, ["good", "who"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "default-token"


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["config", "list", "--format", "json"], {}),
        (["config", "list", "-f", "pipe"], {}),
        (["config", "list"], {"UNTAPED_FORMAT": "json"}),
    ],
)
def test_quarantine_warning_follows_the_requested_format(
    _isolated_config: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    env: dict[str, str],
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    broken = ProviderCandidate(distribution="broken-dist", name="broken", target=lambda: None)
    with pytest.raises(SystemExit) as exit_info:
        bootstrap.run_root(argv, candidates=(broken,))
    assert exit_info.value.code in (0, None)
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [line["level"] for line in lines] == ["warning"]
    assert "'broken-dist' quarantined" in lines[0]["message"]


def test_quarantine_warning_is_text_without_a_structured_format(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = ProviderCandidate(distribution="broken-dist", name="broken", target=lambda: None)
    with pytest.raises(SystemExit):
        bootstrap.run_root(["config", "list"], candidates=(broken,))
    assert capsys.readouterr().err.startswith(
        "warning: plugin 'broken' from 'broken-dist' quarantined"
    )


def test_each_quarantined_plugin_warns_once_by_name(
    broken_first_party_candidates: Callable[[], tuple[ProviderCandidate, ...]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap.compose_root(candidates=broken_first_party_candidates())
    assert capsys.readouterr().err.splitlines() == [
        f"warning: plugin {name!r} from 'untaped' quarantined [malformed-entry-point]: "
        f"could not resolve entry point 'untaped_missing_{name}:p' of distribution 'untaped': "
        f"No module named 'untaped_missing_{name}'"
        for name in ("awx", "jira")
    ]


def test_reset_restores_composed_state(_isolated_config: Path) -> None:
    calls: list[str] = []
    bootstrap.build_root_app(candidates=[_ext_candidate(calls)])
    assert app_context().section("ext", _ExtProfile).token == "default-token"

    set_profile_override("work")
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    with pytest.raises(ConfigError):
        app_context().section("ext", _ExtProfile)

    bootstrap.reset()
    assert profile_override() is None
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
    package = Path(__file__).resolve().parents[1]
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    built = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=package,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stderr
    wheels = sorted(dist_dir.glob("untaped-*-py3-none-any.whl"))
    assert len(wheels) == 1
    expected_version = tomllib.loads((package / "pyproject.toml").read_text())["project"]["version"]
    assert wheels[0].name == f"untaped-{expected_version}-py3-none-any.whl"

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
    assert version.stdout == f"{expected_version}\n"

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
) -> PluginSpec:
    def _factory() -> App:
        calls.append(name)
        if result is not None:
            return result  # type: ignore[return-value]
        return _who_app(name, _token_body_for(name))

    return PluginSpec(
        name=name,
        app_factory=_factory,
        settings=_ExtProfile,
        help=help,
    )


def test_lazy_factory_runs_only_on_dispatch_and_once() -> None:
    calls: list[str] = []
    spec = _counting_spec("lazy", calls, help="Lazy plugin.")
    root = bootstrap.build_root_app(candidates=(provider_candidate(spec),))
    assert calls == []

    listed = CliInvoker().invoke(root.meta, ["--help"])
    assert listed.exit_code == 0, listed.output
    assert "Lazy plugin." in listed.stdout
    assert calls == []

    for _ in range(2):
        result = CliInvoker().invoke(root.meta, ["lazy", "who"])
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == "default-token"
    assert calls == ["lazy"]


def test_eager_factories_are_called_once_per_composition() -> None:
    first_calls: list[str] = []
    second_calls: list[str] = []
    first = make_candidate(_counting_spec("eager", first_calls))
    second = make_candidate(_counting_spec("ext", second_calls))

    root = bootstrap.build_root_app(candidates=[first, second])
    for name in ("eager", "ext"):
        result = CliInvoker().invoke(root.meta, [name, "who"])
        assert result.exit_code == 0, result.output

    assert first_calls == ["eager"]
    assert second_calls == ["ext"]


def _raising_spec(name: str, calls: list[str]) -> PluginSpec:
    def _boom() -> App:
        calls.append(name)
        raise RuntimeError("cli import failed")

    return PluginSpec(
        name=name,
        app_factory=_boom,
        settings=_ExtProfile,
        help=f"{name} plugin.",
    )


def test_a_failing_lazy_factory_fails_only_its_command_in_one_root() -> None:
    bad_calls: list[str] = []
    good_calls: list[str] = []
    root = bootstrap.build_root_app(
        candidates=[
            provider_candidate(_raising_spec("bad", bad_calls), distribution="bad-dist"),
            provider_candidate(_counting_spec("good", good_calls, help="Good plugin.")),
        ]
    )
    assert bad_calls == [] and good_calls == []  # nothing built at startup
    assert bootstrap.composition().quarantine == ()

    bad = CliInvoker().invoke(root.meta, ["bad", "who"])
    assert bad.exit_code == 4
    assert "plugin 'bad' from 'bad-dist' could not build its commands" in bad.stderr
    assert "cli import failed" in bad.stderr

    good = CliInvoker().invoke(root.meta, ["good", "who"])
    assert good.exit_code == 0, good.stderr
    assert good_calls == ["good"]


def test_a_failed_lazy_factory_runs_once_and_fails_every_dispatch() -> None:
    calls: list[str] = []
    root = bootstrap.build_root_app(candidates=[provider_candidate(_raising_spec("bad", calls))])
    for argv in (["bad", "who"], ["bad", "--help"], ["bad"]):
        result = CliInvoker().invoke(root.meta, argv)
        assert result.exit_code == 4, argv
        assert "cli import failed" in result.stderr
    assert calls == ["bad"]


def test_a_lazy_factory_returning_a_non_app_exits_4_on_help_too() -> None:
    calls: list[str] = []
    bad = _counting_spec("bad", calls, help="Bad plugin.", result="not-an-app")
    root = bootstrap.build_root_app(candidates=[provider_candidate(bad)])
    result = CliInvoker().invoke(root.meta, ["bad", "--help"])
    assert result.exit_code == 4
    assert "returned str, expected cyclopts App" in result.stderr


def test_completion_survives_a_plugin_whose_lazy_factory_fails(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    bad_calls: list[str] = []
    root = bootstrap.build_root_app(
        candidates=[
            provider_candidate(_raising_spec("brokencap", bad_calls), distribution="bad-dist"),
            provider_candidate(_counting_spec("goodcap", [], help="Good plugin.")),
        ]
    )
    capsys.readouterr()

    # Through ``--install-completion``'s own path, which calls generate_completion.
    installed = root.install_completion(
        shell="bash", output=tmp_path / "untaped.bash", add_to_startup=False
    )
    script = installed.read_text()

    assert "goodcap" in script
    assert capsys.readouterr().err == ""
    dispatched = CliInvoker().invoke(root.meta, ["brokencap", "who"])
    assert dispatched.exit_code == 4
    assert "cli import failed" in dispatched.stderr
    assert bad_calls == ["brokencap"]


@pytest.mark.parametrize(
    "argv", [["bad", "who", "--format", "json"], ["bad", "--help", "--format=json"]]
)
def test_run_root_reports_a_failing_lazy_factory_as_json_with_exit_4(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as failed:
        bootstrap.run_root(
            argv, candidates=[provider_candidate(_raising_spec("bad", []), distribution="bad-dist")]
        )
    assert failed.value.code == 4
    error = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert (error["level"], error["category"], error["system"], error["exit_code"]) == (
        "error",
        "config",
        "bad",
        4,
    )
    assert error["message"] == (
        "plugin 'bad' from 'bad-dist' could not build its commands: "
        "app factory of plugin 'bad' raised: cli import failed"
    )


#: Private cyclopts internals ``_LazyPluginCommand`` and ``apply_marks`` rely on. Drift here
#: (a cyclopts upgrade within ``>=4.16,<5``) must fail loudly, not render oddly.
_CYCLOPTS_PRIVATE_INTERNALS = (
    "cyclopts.core._apply_parent_defaults_to_app",
    "cyclopts.core.App._commands",
    "cyclopts.core.App._name_transform",
    "cyclopts.command_spec.CommandSpec._resolved",
    "cyclopts.command_spec.CommandSpec.is_resolved",
    "cyclopts.command_spec.CommandSpec.group",
    "cyclopts.core.App._get_item",
    "cyclopts.core.App._meta_parent",
    "cyclopts.core.App.group",
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
                hasattr(spec, "is_resolved"),
                hasattr(spec, "group"),
                callable(getattr(app, "_get_item", None)),
                hasattr(app, "_meta_parent"),
                isinstance(app.group, tuple),
            ),
            strict=True,
        )
        if not present
    ]
    assert not missing, (
        "cyclopts internal API drift: bootstrap._LazyPluginCommand relies on "
        f"{', '.join(missing)}; update it (or pin cyclopts) before upgrading"
    )


@pytest.mark.parametrize(
    ("candidates", "expect_hint", "expect_quarantine"),
    [
        pytest.param([], True, False, id="bare"),
        pytest.param([provider_candidate(make_spec("demo"))], False, False, id="plugin"),
        # entry-point/spec name mismatch
        pytest.param(
            [make_candidate(make_spec("demo"), name="other")], False, True, id="quarantined"
        ),
    ],
)
def test_root_help_install_hint(
    candidates: list[ProviderCandidate], expect_hint: bool, expect_quarantine: bool
) -> None:
    root = bootstrap.build_root_app(candidates=candidates)
    assert bool(bootstrap.composition().quarantine) is expect_quarantine
    result = CliInvoker().invoke(root.meta, ["--help"])
    assert result.exit_code == 0
    help_text = " ".join(result.stdout.split())
    assert (bootstrap.INSTALL_HINT in help_text) is expect_hint


@pytest.mark.usefixtures("_isolated_config")
@pytest.mark.parametrize(
    "argv", [["config", "list"], ["profile", "list"], ["doctor"], ["skills", "list"]]
)
def test_bare_management_commands_work(argv: list[str]) -> None:
    root = bootstrap.build_root_app(candidates=[])
    assert CliInvoker().invoke(root.meta, argv).exit_code == 0


def test_a_plugin_with_only_a_name_mounts_and_registers_nothing() -> None:
    from untaped.management.doctor import collect_doctor_rows
    from untaped.settings import _CONFIG_REGISTRY

    root = bootstrap.build_root_app(candidates=[provider_candidate(PluginSpec(name="bare"))])

    assert [plugin.spec.name for plugin in bootstrap.composition().plugins] == ["bare"]
    assert "bare" not in root
    assert "bare" not in _CONFIG_REGISTRY.profile_sections
    assert "bare" not in _CONFIG_REGISTRY.state_sections
    listed = CliInvoker().invoke(root.meta, ["plugin", "list", "--format", "json"])
    assert [(row["name"], row["status"]) for row in json.loads(listed.stdout)] == [
        ("bare", "ready")
    ]
    rows = collect_doctor_rows(bootstrap.SHELL_SPEC, bootstrap.composition())
    assert [row for row in rows if row["plugin"] == "bare"] == []


def test_a_plugin_with_state_only_registers_its_state_section() -> None:
    from untaped.settings import _CONFIG_REGISTRY

    class _OnlyState(BaseModel):
        last_run: str = ""

    bootstrap.compose_root(
        candidates=[provider_candidate(PluginSpec(name="kept", state=_OnlyState))]
    )

    assert _CONFIG_REGISTRY.state_sections["kept"] is _OnlyState
    assert "kept" not in _CONFIG_REGISTRY.profile_sections


class _ToolsProfile(BaseModel):
    greeting: str = "hi"
    token: str | None = None


def test_a_hyphenated_plugin_reads_its_overrides_with_underscores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from untaped.auth import token_override_env, token_override_name
    from untaped.settings import env_var_name, get_config_section

    spec = PluginSpec(name="acme-tools", settings=_ToolsProfile)
    bootstrap.compose_root(candidates=[provider_candidate(spec)])
    monkeypatch.setenv("UNTAPED_ACME_TOOLS__GREETING", "from env")
    monkeypatch.setenv("UNTAPED_ACME_TOOLS__TOKEN", "env-token")
    get_settings.cache_clear()

    settings = get_config_section("acme-tools", _ToolsProfile)

    assert (settings.greeting, settings.token) == ("from env", "env-token")
    assert env_var_name(["acme-tools", "greeting"]) == "UNTAPED_ACME_TOOLS__GREETING"
    assert token_override_name("acme-tools") == "UNTAPED_ACME_TOOLS__TOKEN"
    assert token_override_env("acme-tools") == "UNTAPED_ACME_TOOLS__TOKEN"


def test_a_hyphenated_plugin_reads_its_json_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from untaped.settings import get_config_section

    spec = PluginSpec(name="acme-tools", settings=_ToolsProfile)
    bootstrap.compose_root(candidates=[provider_candidate(spec)])
    monkeypatch.setenv("UNTAPED_ACME_TOOLS", '{"greeting": "blob"}')
    get_settings.cache_clear()

    assert get_config_section("acme-tools", _ToolsProfile).greeting == "blob"


def test_the_root_mounts_exactly_the_reserved_management_commands() -> None:
    from untaped.plugins.registry import RESERVED_COMMAND_GROUPS, ROOT_MANAGEMENT_COMMANDS

    root = bootstrap.build_root_app(candidates=[])

    mounted = [name for name in root if not name.startswith("-")]
    assert mounted == list(ROOT_MANAGEMENT_COMMANDS)
    assert set(mounted) <= RESERVED_COMMAND_GROUPS


def test_no_plugin_can_claim_a_management_command() -> None:
    root = bootstrap.build_root_app(candidates=[])
    management = [name for name in root if not name.startswith("-")]
    assert "auth" in management
    for name in management:
        bootstrap.build_root_app(candidates=[make_candidate(make_spec(name))])
        assert [r.reason for r in bootstrap.composition().quarantine] == ["reserved-name"], name


def test_the_console_entry_point_forwards_signals_to_git_before_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(bootstrap, "forward_signals", lambda: calls.append("forward"))
    monkeypatch.setattr(bootstrap, "run_root", lambda argv: calls.append(argv))

    bootstrap.main(["--version"])

    assert calls == ["forward", ["--version"]]
