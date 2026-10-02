"""Tests for the root ``untaped config …`` command group (Wave 1.4, spec §4).

Root resolution is direct, not delegated per tool: a fully qualified
``section.key`` selects its schema by ``section``. SDK roots win first,
state-managed fields are rejected per the section's own ``state_model``,
and bare keys are NEVER implicitly expanded to a capability section.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from test_management.support import (
    GithubProfile,
    GithubState,
    JiraProfile,
    compose,
    make_spec,
    write_config,
)
from untaped import bootstrap
from untaped.config_file import read_config_dict
from untaped.management.config import (
    build_root_config_app,
)
from untaped.settings import get_settings
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _config_app() -> object:
    github = make_spec("github", profile_model=GithubProfile, state_model=GithubState)
    jira = make_spec("jira", profile_model=JiraProfile)
    result = compose(github, jira)
    return build_root_config_app(shell=bootstrap.SHELL_SPEC, result=result)


# ── fully-qualified keys ─────────────────────────────────────────────────────


def test_set_fully_qualified_key_writes_section(
    _isolated_config: Path,
) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "github.token", "ghp_x"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "set github.token in profile default" in result.output
    assert read_config_dict(_isolated_config)["profiles"]["default"]["github"]["token"] == "ghp_x"


def test_get_fully_qualified_key_reads_section(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    jira:\n      base_url: https://j\n")
    get_settings.cache_clear()
    app = _config_app()
    result = CliInvoker().invoke(app, ["get", "jira.base_url"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "https://j" in result.stdout


def test_list_raw_is_the_key_stream_of_every_composed_section(_isolated_config: Path) -> None:
    """Raw list output is the stable key stream when columns are omitted."""
    app = _config_app()
    result = CliInvoker().invoke(app, ["list", "--format", "raw"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    keys = set(result.stdout.splitlines())
    assert {"github.token", "github.base_url", "jira.base_url", "skills.updates"} <= keys
    assert "https://api.github.com" not in keys


def test_list_all_profiles_raw_is_empty_without_profiles(_isolated_config: Path) -> None:
    """The raw all-profiles stream has no rows before a profile is created."""
    app = _config_app()
    result = CliInvoker().invoke(
        app,
        ["list", "--all-profiles", "--format", "raw"],  # type: ignore[arg-type]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == ""


def test_unset_fully_qualified_key_removes_value(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    github:\n      mode: on\n")
    get_settings.cache_clear()
    app = _config_app()
    result = CliInvoker().invoke(app, ["unset", "github.mode"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "unset github.mode" in result.output
    assert "github" not in read_config_dict(_isolated_config)["profiles"]["default"]


# ── SDK roots win ────────────────────────────────────────────────────────────


def test_sdk_root_key_resolves_to_sdk_settings(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "http.verify_ssl", "false"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "set http.verify_ssl in profile default" in result.output
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["default"]["http"] == {"verify_ssl": False}
    assert "http" not in data["profiles"]["default"].get("github", {})


# ── state writes rejected per section schema ─────────────────────────────────


@pytest.mark.parametrize(
    "argv", [["set", "github.cursor", "abc"], ["get", "github.cursor"], ["unset", "github.cursor"]]
)
def test_state_field_is_rejected_without_writing(_isolated_config: Path, argv: list[str]) -> None:
    result = CliInvoker().invoke(_config_app(), argv)  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "github.cursor" in result.output
    assert "managed by" in result.output
    assert not _isolated_config.exists()


# ── NO bare-key implicit expansion ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("argv", "named"),
    [
        # ``token`` must NOT resolve to ``github.token`` at the root.
        (["set", "token", "ghp_x"], "token"),
        (["get", "bogus"], "bogus"),
        (["get", "nope.key"], "nope.key"),
    ],
    ids=["bare-capability-key", "bare-unknown-key", "unknown-section"],
)
def test_unresolvable_key_is_rejected(_isolated_config: Path, argv: list[str], named: str) -> None:
    result = CliInvoker().invoke(_config_app(), argv)  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert named in result.output
    assert not _isolated_config.exists()


# ── repair path (failure isolation) ──────────────────────────────────────────


def test_set_repairing_invalid_value_succeeds(_isolated_config: Path) -> None:
    """A schema-rejected value is fixable via ``config set`` on the same key."""
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    jira:\n      timeout: not-a-number\n",
    )
    get_settings.cache_clear()
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "jira.timeout", "12"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert read_config_dict(_isolated_config)["profiles"]["default"]["jira"]["timeout"] == 12


def test_list_all_profiles_shows_each_profile(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    github:\n      base_url: https://d\n"
        "  prod:\n    github:\n      base_url: https://p\n",
    )
    get_settings.cache_clear()
    app = _config_app()
    result = CliInvoker().invoke(
        app,  # type: ignore[arg-type]
        ["list", "--all-profiles", "--format", "raw", "--columns", "profile"],
    )
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.splitlines()) == ["default", "prod"]


def test_edit_without_editor_is_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    app = _config_app()
    result = CliInvoker().invoke(app, ["edit"])  # type: ignore[arg-type]
    assert result.exit_code == 4  # no editor configured: the environment
    assert "VISUAL" in result.output or "EDITOR" in result.output


def _scripted_editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | bytes | None, *, code: str = ""
) -> None:
    """Point $VISUAL at an editor that runs ``code`` on ``p`` then writes ``content``."""
    import shlex
    import sys
    import tempfile

    # Edited copies kept after a failed save land in tmp_path, not /tmp.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    script = tmp_path / "config editor.py"
    data = content.encode() if isinstance(content, str) else content
    save = "" if data is None else f"p.write_bytes({data!r})\n"
    script.write_text(f"import pathlib, sys\np = pathlib.Path(sys.argv[-1])\n{code}\n{save}")
    monkeypatch.setenv("VISUAL", shlex.join([sys.executable, str(script)]))


@pytest.mark.parametrize(
    "content",
    [
        "[invalid",
        "profiles: {default: {jira: {timeout: not-a-number}}}",
    ],
)
def test_config_edit_rejects_an_invalid_edit_and_keeps_the_config(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    original = "# mine\nprofiles: {default: {jira: {timeout: 5}}}\n"
    write_config(_isolated_config, original)
    _scripted_editor(tmp_path, monkeypatch, content)
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 1, result.output
    assert _isolated_config.read_text() == original
    assert "saved and validated" not in result.output
    kept = Path(result.stderr.split("your edits are in ")[1].split()[0])
    assert kept.read_text() == content


def test_config_edit_never_opens_a_newer_config(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = "format_version: 2\nprofiles: {}\n"
    write_config(_isolated_config, original)
    marker = tmp_path / "editor-ran"
    _scripted_editor(
        tmp_path, monkeypatch, "profiles: {}\n", code=f"pathlib.Path({str(marker)!r}).touch()"
    )
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 4, result.output
    assert "written by a newer untaped (format 2" in result.stderr
    assert _isolated_config.read_text() == original
    assert not marker.exists()


def test_config_edit_repairs_an_invalid_stamp(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "format_version: true\nprofiles: {}\n")
    _scripted_editor(tmp_path, monkeypatch, "format_version: 1\nprofiles: {}\n")
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 0, result.output
    assert _isolated_config.read_text() == "format_version: 1\nprofiles: {}\n"


def test_config_edit_names_a_newer_draft_format(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "profiles: {}\n")
    _scripted_editor(tmp_path, monkeypatch, "format_version: 2\nprofiles: {}\n")
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 1, result.output
    assert "format_version 2 is newer than this release supports (format 1)" in result.stderr
    assert "upgrade untaped" not in result.stderr
    assert _isolated_config.read_text() == "profiles: {}\n"
    kept = Path(result.stderr.split("your edits are in ")[1].split()[0])
    assert kept.read_text() == "format_version: 2\nprofiles: {}\n"


def test_config_edit_saves_verbatim_owner_only_and_through_a_symlink(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "dotfiles" / "config.yml"
    real.parent.mkdir()
    real.write_text("profiles: {}\n")
    real.chmod(0o644)
    _isolated_config.unlink(missing_ok=True)
    _isolated_config.parent.mkdir(parents=True, exist_ok=True)
    _isolated_config.symlink_to(real)
    content = "# hand edited\nprofiles: {default: {jira: {timeout: 12}}}"
    _scripted_editor(tmp_path, monkeypatch, content)
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 0, result.output
    assert "saved and validated" in result.output
    assert _isolated_config.is_symlink()
    assert real.read_text() == content
    assert real.stat().st_mode & 0o777 == 0o600


def test_config_edit_invalid_edit_never_writes_the_config(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "profiles: {}\n")
    pinned = tmp_path / "pinned.yml"
    os.link(_isolated_config, pinned)  # an atomic replace would break this hard link
    _scripted_editor(tmp_path, monkeypatch, "profiles: {default: {jira: {timeout: nope}}}")
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 1, result.output
    assert os.path.samefile(_isolated_config, pinned)


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX permissions")
def test_config_edit_keeps_the_draft_when_saving_fails(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "readonly" / "config.yml"
    real.parent.mkdir()
    real.write_text("profiles: {}\n")
    real.parent.chmod(0o555)
    _isolated_config.parent.mkdir(parents=True, exist_ok=True)
    _isolated_config.symlink_to(real)
    content = "profiles: {default: {jira: {timeout: 12}}}"
    _scripted_editor(tmp_path, monkeypatch, content)
    try:
        result = CliInvoker().invoke(_config_app(), ["edit"])
    finally:
        real.parent.chmod(0o755)
    assert result.exit_code == 4, result.output  # the save failed: the environment
    assert real.read_text() == "profiles: {}\n"
    kept = Path(result.stderr.split("your edits are in ")[1].split()[0])
    assert kept.read_text() == content


def test_config_edit_keeps_the_draft_when_it_is_not_utf8(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "profiles: {}\n")
    _scripted_editor(tmp_path, monkeypatch, b"profiles: {}  # \xff\n")
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 1, result.output
    assert _isolated_config.read_text() == "profiles: {}\n"
    kept = Path(result.stderr.split("your edits are in ")[1].split()[0])
    assert kept.read_bytes() == b"profiles: {}  # \xff\n"


def test_config_edit_keeps_edits_saved_before_the_editor_failed(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "profiles: {}\n")
    content = "# saved user edits\nprofiles: {}\n"
    _scripted_editor(tmp_path, monkeypatch, None, code=f"p.write_text({content!r})\nsys.exit(1)")
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 1, result.output
    assert "editor exited with status 1" in result.stderr
    assert _isolated_config.read_text() == "profiles: {}\n"
    kept = Path(result.stderr.split("your edits are in ")[1].split()[0])
    assert kept.read_text() == content
    assert kept.stat().st_mode & 0o777 == 0o600
    assert f"copy {kept} over {_isolated_config}" in result.stderr


def test_config_edit_drops_an_unchanged_copy_when_the_editor_fails(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "profiles: {}\n")
    _scripted_editor(tmp_path, monkeypatch, None, code="sys.exit(1)")
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 1, result.output
    assert "editor exited with status 1" in result.stderr
    assert "your edits" not in result.stderr
    assert not list(tmp_path.glob("untaped-config-edit-*"))


def test_config_edit_keeps_crlf_line_endings(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_config.write_bytes(b"profiles:\r\n  default: {jira: {timeout: 5}}\r\n")
    _scripted_editor(
        tmp_path,
        monkeypatch,
        None,
        code="p.write_bytes(p.read_bytes().replace(b'timeout: 5', b'timeout: 12'))",
    )
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 0, result.output
    assert _isolated_config.read_bytes() == b"profiles:\r\n  default: {jira: {timeout: 12}}\r\n"


def test_config_edit_without_changes_writes_nothing(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, "profiles: {}\n")
    pinned = tmp_path / "pinned.yml"
    os.link(_isolated_config, pinned)  # an atomic replace would break this hard link
    _scripted_editor(tmp_path, monkeypatch, None)
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert result.exit_code == 0, result.output
    assert "no changes" in result.stderr
    assert "saved" not in result.output
    assert os.path.samefile(_isolated_config, pinned)


def test_config_edit_saves_under_the_config_lock(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from filelock import FileLock

    write_config(_isolated_config, "profiles: {}\n")
    monkeypatch.setenv("UNTAPED_CONFIG_LOCK_TIMEOUT", "0.05")
    _scripted_editor(tmp_path, monkeypatch, "profiles: {default: {jira: {timeout: 12}}}")
    held = FileLock(f"{_isolated_config}.lock")
    held.acquire()
    try:
        result = CliInvoker().invoke(_config_app(), ["edit"])
    finally:
        held.release()
    assert result.exit_code == 5, result.output  # the lock is busy: retry later
    assert "could not acquire lock" in result.stderr
    assert _isolated_config.read_text() == "profiles: {}\n"


def test_set_preserves_comments_and_key_order(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "# hand edited\n"
        "profiles:\n"
        "  # shared base\n"
        "  default:\n"
        "    jira:\n"
        "      timeout: 5  # seconds\n"
        "    github:\n"
        "      token: keep  # rotate monthly\n",
    )
    app = _config_app()

    result = CliInvoker().invoke(app, ["set", "github.base_url", "https://ghe.example"])  # type: ignore[arg-type]

    assert result.exit_code == 0, result.output
    assert _isolated_config.read_text(encoding="utf-8") == (
        "# hand edited\n"
        "profiles:\n"
        "  # shared base\n"
        "  default:\n"
        "    jira:\n"
        "      timeout: 5  # seconds\n"
        "    github:\n"
        "      token: keep  # rotate monthly\n"
        "      base_url: https://ghe.example\n"
    )


# ── outcome records and --dry-run ────────────────────────────────────────────


def _json(result: object) -> object:
    return json.loads(result.stdout)  # type: ignore[attr-defined]


def test_set_emits_a_setting_outcome(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "github.token", "ghp_x", "-f", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert _json(result) == {"key": "github.token", "profile": "default", "action": "updated"}
    assert "ghp_x" not in result.stdout


def test_set_dry_run_validates_without_writing(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(  # type: ignore[arg-type]
        app, ["set", "jira.timeout", "12", "--dry-run", "-f", "json"]
    )
    assert result.exit_code == 0, result.output
    assert _json(result) == {"key": "jira.timeout", "profile": "default", "action": "planned"}
    assert not _isolated_config.exists()

    invalid = CliInvoker().invoke(app, ["set", "jira.timeout", "soon", "--dry-run"])  # type: ignore[arg-type]
    assert invalid.exit_code == 1
    assert "invalid value for 'jira.timeout'" in invalid.stderr


def test_unset_emits_deleted_unchanged_and_planned(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    github:\n      mode: 'on'\n")
    app = _config_app()

    def unset(*extra: str) -> object:
        result = CliInvoker().invoke(app, ["unset", "github.mode", "-f", "json", *extra])  # type: ignore[arg-type]
        assert result.exit_code == 0, result.output
        return _json(result)["action"]  # type: ignore[index]

    assert unset("--dry-run") == "planned"
    assert read_config_dict(_isolated_config)["profiles"]["default"]["github"] == {"mode": "on"}
    assert unset() == "deleted"
    assert unset() == "unchanged"
    assert unset("--dry-run") == "unchanged"
