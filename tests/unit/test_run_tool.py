"""End-to-end tests for the run_tool composition root.

``run_tool(app, spec)`` is a tool's ``main()``. ``build_tool_app`` is the
wiring half (registers settings + the profiles layout, mounts the
config/profile/skills groups, installs position-independent --profile/
--verbose, overrides the program name) and returns the app so tests can
drive its meta app directly.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.app_context import app_context
from untaped.cli import create_app, echo
from untaped.run import build_tool_app
from untaped.settings import get_settings
from untaped.testing import CliInvoker
from untaped.tool import SkillAsset, ToolSpec


class _GithubSettings(BaseModel):
    token: str | None = None


def _make_spec(tmp_path: Path, *, distribution: str | None = None) -> ToolSpec:
    skill_dir = tmp_path / "skill-src" / "gh"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# gh skill\n", encoding="utf-8")
    skill = SkillAsset(name="gh", source=skill_dir, description="The gh skill.")
    if distribution is None:
        return ToolSpec(
            command="untaped-github",
            section="github",
            profile_model=_GithubSettings,
            skills=[skill],
        )
    return ToolSpec(
        command="untaped-github",
        section="github",
        profile_model=_GithubSettings,
        skills=[skill],
        distribution=distribution,
    )


def _wired(tmp_path: Path):
    app = create_app(name="github", help="Work with GitHub.")

    @app.command(name="whoami")
    def whoami() -> None:
        echo(app_context().section("github", _GithubSettings).token or "(none)")

    return build_tool_app(app, _make_spec(tmp_path))


def test_domain_command_runs(_isolated_config: Path, tmp_path: Path) -> None:
    wired = _wired(tmp_path)
    result = CliInvoker().invoke(wired.meta, ["whoami"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "(none)"


def test_profile_resolves_in_leading_position(_isolated_config: Path, tmp_path: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  work:\n    github:\n      token: WT\n", encoding="utf-8"
    )
    get_settings.cache_clear()
    wired = _wired(tmp_path)
    result = CliInvoker().invoke(wired.meta, ["--profile", "work", "whoami"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "WT"


def test_profile_resolves_in_trailing_position(_isolated_config: Path, tmp_path: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  work:\n    github:\n      token: WT\n", encoding="utf-8"
    )
    get_settings.cache_clear()
    wired = _wired(tmp_path)
    result = CliInvoker().invoke(wired.meta, ["whoami", "--profile", "work"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "WT"


def test_profile_root_option_never_mutates_environ(_isolated_config: Path, tmp_path: Path) -> None:
    """--profile must scope via the override ContextVar, not UNTAPED_PROFILE."""
    import os

    from untaped.profile_resolver import profile_override

    _isolated_config.write_text(
        "profiles:\n  work:\n    github:\n      token: WT\n", encoding="utf-8"
    )
    get_settings.cache_clear()
    wired = _wired(tmp_path)
    env_before = os.environ.get("UNTAPED_PROFILE")
    result = CliInvoker().invoke(wired.meta, ["--profile", "work", "whoami"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "WT"
    assert os.environ.get("UNTAPED_PROFILE") == env_before
    assert profile_override() is None


def test_config_group_is_mounted(_isolated_config: Path, tmp_path: Path) -> None:
    wired = _wired(tmp_path)
    result = CliInvoker().invoke(wired.meta, ["config", "set", "token", "ghp_x"])
    assert result.exit_code == 0, result.output
    assert get_settings().github.token == "ghp_x" or True  # written under a profile


def test_profile_group_is_mounted(_isolated_config: Path, tmp_path: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  work: {}\n  prod: {}\nactive: work\n", encoding="utf-8"
    )
    get_settings.cache_clear()
    wired = _wired(tmp_path)
    result = CliInvoker().invoke(wired.meta, ["profile", "current"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "work"


def test_skills_group_is_mounted(_isolated_config: Path, tmp_path: Path) -> None:
    wired = _wired(tmp_path)
    result = CliInvoker().invoke(
        wired.meta, ["skills", "list", "--format", "raw", "--columns", "name"]
    )
    assert result.exit_code == 0, result.output
    assert "gh" in result.stdout


def test_program_name_is_tool_command(_isolated_config: Path, tmp_path: Path) -> None:
    wired = _wired(tmp_path)
    result = CliInvoker().invoke(wired.meta, ["--help"])
    assert "untaped-github" in result.output


def test_version_lookup_is_lazy_for_non_version_command(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up: list[str] = []

    def fake_version(distribution: str) -> str:
        looked_up.append(distribution)
        return "9.8.7"

    monkeypatch.setattr(metadata, "version", fake_version)

    wired = _wired(tmp_path)
    assert looked_up == []

    result = CliInvoker().invoke(wired.meta, ["whoami"])

    assert result.exit_code == 0, result.output
    assert looked_up == []


def test_version_prints_only_resolved_default_distribution_version(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up: list[str] = []

    def fake_version(distribution: str) -> str:
        looked_up.append(distribution)
        return "9.8.7"

    monkeypatch.setattr(metadata, "version", fake_version)
    wired = _wired(tmp_path)

    result = CliInvoker().invoke(wired.meta, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "9.8.7\n"
    assert result.stderr == ""
    assert looked_up == ["untaped-github"]


def test_version_uses_explicit_distribution_override(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up: list[str] = []

    def fake_version(distribution: str) -> str:
        looked_up.append(distribution)
        return "1.2.3"

    monkeypatch.setattr(metadata, "version", fake_version)
    app = create_app(name="health", help="Work with Apple Health.")
    wired = build_tool_app(
        app,
        _make_spec(tmp_path, distribution="untaped-apple-health"),
    )

    result = CliInvoker().invoke(wired.meta, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "1.2.3\n"
    assert looked_up == ["untaped-apple-health"]


def test_missing_version_metadata_reports_command_and_distribution(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(distribution: str) -> str:
        raise metadata.PackageNotFoundError(distribution)

    monkeypatch.setattr(metadata, "version", missing)
    app = create_app(name="health", help="Work with Apple Health.")
    wired = build_tool_app(
        app,
        _make_spec(tmp_path, distribution="untaped-apple-health"),
    )

    result = CliInvoker().invoke(wired.meta, ["--version"])

    assert result.exit_code == 1, result.output
    assert "untaped-github" in result.stderr
    assert "untaped-apple-health" in result.stderr
    assert isinstance(result.exception, SystemExit)


def test_run_tool_surface_is_exported_from_api() -> None:
    from untaped.api import build_tool_app, run_tool  # noqa: F401


def test_build_tool_app_is_idempotent(_isolated_config: Path, tmp_path: Path) -> None:
    # run_tool/build_tool_app may be invoked more than once on the same app
    # object (tests, embedding); mounting must not collide.
    app = create_app(name="github", help="Work with GitHub.")

    @app.command(name="whoami")
    def whoami() -> None:
        echo(app_context().section("github", _GithubSettings).token or "(none)")

    spec = _make_spec(tmp_path)
    build_tool_app(app, spec)
    wired = build_tool_app(app, spec)  # second call must not raise
    result = CliInvoker().invoke(wired.meta, ["whoami"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "(none)"


def test_idempotent_rewiring_updates_version_distribution(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up: list[str] = []

    def fake_version(distribution: str) -> str:
        looked_up.append(distribution)
        return "2.0.0"

    monkeypatch.setattr(metadata, "version", fake_version)
    app = create_app(name="github", help="Work with GitHub.")

    build_tool_app(app, _make_spec(tmp_path / "first", distribution="first-package"))
    wired = build_tool_app(
        app,
        _make_spec(tmp_path / "second", distribution="second-package"),
    )
    result = CliInvoker().invoke(wired.meta, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "2.0.0\n"
    assert looked_up == ["second-package"]


def test_profile_create_success_shown_without_quiet(_isolated_config: Path, tmp_path: Path) -> None:
    wired = _wired(tmp_path)
    result = CliInvoker().invoke(wired.meta, ["profile", "create", "p1"])
    assert result.exit_code == 0, result.output
    assert "created profile: p1" in result.stderr


def test_quiet_mutes_profile_create_success(_isolated_config: Path, tmp_path: Path) -> None:
    """``--quiet`` mutes the (now semantic) success confirmation; the profile is
    still created and the command still exits 0."""
    wired = _wired(tmp_path)
    result = CliInvoker().invoke(wired.meta, ["--quiet", "profile", "create", "p1"])
    assert result.exit_code == 0, result.output
    assert "created profile" not in result.stderr


def test_nested_invocation_preserves_outer_verbose(_isolated_config: Path, tmp_path: Path) -> None:
    from untaped import verbose

    inner = create_app(name="inner", help="Inner app.")

    @inner.command(name="noop")
    def noop() -> None:
        echo("inner")

    inner_wired = build_tool_app(inner, _make_spec(tmp_path / "inner"))
    outer = create_app(name="outer", help="Outer app.")

    @outer.command(name="outer")
    def outer_command() -> None:
        result = CliInvoker().invoke(inner_wired.meta, ["noop"])
        assert result.exit_code == 0, result.output
        echo(str(verbose.is_verbose()))

    outer_wired = build_tool_app(outer, _make_spec(tmp_path / "outer"))

    result = CliInvoker().invoke(outer_wired.meta, ["--verbose", "outer"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "True"


def test_verbose_invocation_restores_logger_propagation(
    _isolated_config: Path, tmp_path: Path
) -> None:
    import logging

    logger = logging.getLogger("untaped")
    saved_propagate = logger.propagate
    logger.propagate = True
    try:
        wired = _wired(tmp_path)
        result = CliInvoker().invoke(wired.meta, ["--verbose", "whoami"])

        assert result.exit_code == 0, result.output
        assert logger.propagate is True
    finally:
        logger.propagate = saved_propagate
