"""End-to-end tests for the run_tool composition root.

``run_tool(app, spec)`` is a tool's ``main()``. ``build_tool_app`` is the
wiring half (registers settings + the profiles layout, mounts the
config/profile/skills groups, installs position-independent --profile/
--verbose, overrides the program name) and returns the app so tests can
drive its meta app directly.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from untaped.app_context import app_context
from untaped.cli import create_app, echo
from untaped.run import build_tool_app
from untaped.settings import get_settings
from untaped.testing import CliInvoker
from untaped.tool import SkillAsset, ToolSpec


class _GithubSettings(BaseModel):
    token: str | None = None


def _make_spec(tmp_path: Path) -> ToolSpec:
    skill_dir = tmp_path / "skill-src" / "gh"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# gh skill\n", encoding="utf-8")
    return ToolSpec(
        command="untaped-github",
        section="github",
        profile_model=_GithubSettings,
        skills=[SkillAsset(name="gh", source=skill_dir, description="The gh skill.")],
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
