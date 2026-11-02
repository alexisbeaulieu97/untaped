"""Tests for the folder layer: a project's ``.untaped/config.yml`` read beneath the run's picks.

Lowest first: the base, the folder's picks, the folder's own values,
``UNTAPED_PROFILE``'s picks, ``--profile``'s picks, then variables. The file
counts only beside ``project.json``, only when it is yours and nobody else
can write it, and may not set secrets, commands or ``HomeOnly()`` settings.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

import pytest
from pydantic import BaseModel, SecretStr

import untaped.runtime.folder_layer as folder_layer_module
from untaped.runtime.folder_layer import folder_layer, forget_folder_layer, untrusted
from untaped.runtime.profile_resolver import current_pick, explicit_pick, settings_key
from untaped.runtime.run import entered, start_run
from untaped.runtime.settings import (
    active_settings_layout,
    get_settings,
    load_settings_section,
    register_profile_settings,
)
from untaped.sdk._schema import Argv
from untaped.sdk.errors import ConfigError
from untaped.sdk.health import HomeOnly
from untaped.sdk.testing import make_project

pytestmark = pytest.mark.usefixtures("_isolated_config")


class _Server(BaseModel):
    url: str | None = None


class _Demo(BaseModel):
    region: str = "eu"
    org: str | None = None
    folder: Path | None = None
    server: _Server = _Server()
    token: SecretStr | None = None
    command: Annotated[list[str] | None, Argv()] = None
    store_dir: Annotated[Path | None, HomeOnly()] = None


HOME = """\
format_version: 2
demo: {region: base, org: home-org}
profiles:
  staging: {demo: {region: staging, server: {url: https://staging.example.com}}}
  prod: {demo: {region: prod}}
  jira-beta: {demo: {org: beta}}
"""


@pytest.fixture(autouse=True)
def _demo(_isolated_config: Path) -> Iterator[None]:
    _isolated_config.write_text(HOME, encoding="utf-8")
    register_profile_settings("demo", _Demo)
    get_settings.cache_clear()
    forget_folder_layer()
    yield
    forget_folder_layer()


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str | None) -> Path:
    root = tmp_path / "PROJ-123"
    make_project(root)
    if text is not None:
        file = root / ".untaped" / "config.yml"
        file.write_text(text, encoding="utf-8")
        file.chmod(0o600)
        file.parent.chmod(0o700)
    monkeypatch.chdir(root)
    return root


def _demo_now(*profiles: str) -> _Demo:
    with entered(start_run(profiles=profiles)):
        return load_settings_section("demo")


def test_the_folders_pick_and_values_layer_over_the_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, "profile: staging\ndemo: {org: Platform}\n")
    demo = _demo_now()
    assert (demo.region, demo.org, demo.server.url) == (
        "staging",
        "Platform",
        "https://staging.example.com",
    )


def test_a_command_pick_beats_the_folders_value_and_stacks_without_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, monkeypatch, "profile: staging\ndemo: {org: Platform}\n")
    demo = _demo_now("jira-beta")
    assert demo.org == "beta"  # the flag's pick beats the folder's own value
    assert demo.server.url == "https://staging.example.com"  # and stacks on its pick
    monkeypatch.setenv("UNTAPED_PROFILE", "prod")
    assert _demo_now().region == "prod"
    with entered(start_run(profiles=("jira-beta",))):
        assert [(pick.name, pick.source) for pick in current_pick()] == [
            ("staging", "project"),
            ("prod", "env"),
            ("jira-beta", "flag"),
        ]
        assert explicit_pick() == ("prod", "jira-beta")
    assert "warning" not in capsys.readouterr().err


def test_default_sets_the_folders_pick_aside_but_not_its_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, "profile: staging\ndemo: {org: Platform}\n")
    demo = _demo_now("default")
    assert (demo.region, demo.org) == ("base", "Platform")
    monkeypatch.setenv("UNTAPED_PROFILE", "default")
    demo = _demo_now()
    assert (demo.region, demo.org) == ("base", "Platform")


def test_variables_beat_the_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch, "demo: {org: Platform}\n")
    monkeypatch.setenv("UNTAPED_DEMO__ORG", "from-env")
    assert _demo_now().org == "from-env"


def test_provenance_names_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch, "profile: [staging]\ndemo: {org: Platform}\n")
    with entered(start_run()):
        provenance = active_settings_layout().resolve(_home()).provenance
    assert provenance[("demo", "org")] == "project:PROJ-123"
    assert provenance[("demo", "region")] == "staging"


def test_a_profile_by_name_reads_no_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, "profile: staging\ndemo: {org: Platform}\n")
    with entered(start_run()):
        effective = active_settings_layout().effective(_home(), profile="prod")
    assert effective["demo"] == {"region": "prod", "org": "home-org"}


def test_an_unknown_folder_pick_is_skipped_with_one_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, monkeypatch, "profile: [ghost, staging]\n")
    assert _demo_now().region == "staging"
    assert _demo_now().region == "staging"
    err = capsys.readouterr().err
    assert err.count("picks profile 'ghost'") == 1
    with entered(start_run()):
        layer = folder_layer()
    assert layer is not None
    assert (layer.picks, layer.skipped) == (("staging",), ("ghost",))


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("demo: {token: s3cret}\n", "demo.token"),
        ("demo: {command: [get-token]}\n", "demo.command"),
        ("demo: {store_dir: /srv/store}\n", "demo.store_dir"),
    ],
)
def test_a_setting_only_the_home_holds_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, key: str
) -> None:
    _project(tmp_path, monkeypatch, text)
    with pytest.raises(ConfigError) as refused:
        _demo_now()
    assert refused.value.exit_code == 4
    assert key in str(refused.value)


@pytest.mark.parametrize("key", ["profiles", "active"])
def test_a_project_defines_no_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    _project(tmp_path, monkeypatch, f"{key}: {{}}\n")
    with pytest.raises(ConfigError, match=f"`{key}:`") as refused:
        _demo_now()
    assert refused.value.exit_code == 4


def test_a_newer_format_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch, "format_version: 99\ndemo: {org: x}\n")
    with pytest.raises(ConfigError, match="newer untaped") as refused:
        _demo_now()
    assert refused.value.exit_code == 4


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX permissions")
def test_a_file_others_can_write_is_ignored_with_one_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path, monkeypatch, "demo: {org: Platform}\n")
    (root / ".untaped" / "config.yml").chmod(0o666)
    assert _demo_now().org == "home-org"
    assert _demo_now().org == "home-org"
    err = capsys.readouterr().err
    assert err.count("ignoring") == 1
    assert "chmod go-w" in err


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX permissions")
def test_a_folder_others_can_write_is_not_trusted(tmp_path: Path) -> None:
    folder = tmp_path / ".untaped"
    folder.mkdir()
    file = folder / "config.yml"
    file.write_text("{}\n", encoding="utf-8")
    file.chmod(0o600)
    folder.chmod(0o777)
    assert untrusted(file) is not None
    folder.chmod(0o700)
    assert untrusted(file) is None


def test_a_relative_path_is_read_from_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, "demo: {folder: notes/out}\n")
    monkeypatch.chdir(root / ".untaped")
    assert _demo_now().folder == root / "notes" / "out"


def test_a_config_file_without_a_project_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "clone" / ".untaped"
    folder.mkdir(parents=True)
    (folder / "config.yml").write_text("demo: {org: planted}\n", encoding="utf-8")
    monkeypatch.chdir(folder.parent)
    assert folder_layer() is None
    assert _demo_now().org == "home-org"


def test_a_profile_named_project_is_still_a_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_config: Path
) -> None:
    _isolated_config.write_text(HOME + "  project: {demo: {org: named-project}}\n")
    _project(tmp_path, monkeypatch, "profile: project\n")
    with entered(start_run()):
        provenance = active_settings_layout().resolve(_home()).provenance
    assert provenance[("demo", "org")] == "project"


def test_settings_are_cached_per_project_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, "demo: {org: one}\n")
    with entered(start_run()):
        before = settings_key()
        assert get_settings().demo.org == "one"  # type: ignore[attr-defined]
        file = root / ".untaped" / "config.yml"
        file.write_text("demo: {org: two-two}\n", encoding="utf-8")
        assert settings_key() != before
        assert get_settings().demo.org == "two-two"  # type: ignore[attr-defined]


def _home() -> dict[str, object]:
    from untaped.runtime.config_file import read_config_dict

    return read_config_dict()


def test_a_copied_project_s_file_is_ignored_until_the_copy_is_this_home_s_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path, monkeypatch, "demo: {org: Platform}\n")
    copy = tmp_path / "planted"
    shutil.copytree(root, copy)  # a project.json no reference of this home records here
    monkeypatch.chdir(copy)
    forget_folder_layer()
    with entered(start_run()):
        assert folder_layer() is None
    assert _demo_now().org == "home-org"
    err = capsys.readouterr().err
    assert err.count("ignoring") == 1
    assert f"untaped project init {copy}" in err


def test_the_ignored_file_warns_once_even_after_the_home_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    _isolated_config: Path,
) -> None:
    root = _project(tmp_path, monkeypatch, "demo: {org: Platform}\n")
    copy = tmp_path / "planted"
    shutil.copytree(root, copy)
    monkeypatch.chdir(copy)
    forget_folder_layer()
    assert _demo_now().org == "home-org"
    _isolated_config.write_text(HOME + "  extra: {}\n", encoding="utf-8")
    get_settings.cache_clear()
    assert _demo_now().org == "home-org"
    assert capsys.readouterr().err.count("ignoring") == 1


def _accounts(
    monkeypatch: pytest.MonkeyPatch, group: str, members: list[str], primary: list[int]
) -> None:
    grp = pytest.importorskip("grp")
    pwd = pytest.importorskip("pwd")
    me = SimpleNamespace(pw_name="me", pw_uid=1000, pw_gid=1000)
    others = [SimpleNamespace(pw_name=f"u{uid}", pw_uid=uid, pw_gid=1000) for uid in primary]
    monkeypatch.setattr(
        grp, "getgrgid", lambda _gid: SimpleNamespace(gr_name=group, gr_mem=members)
    )
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: me)
    monkeypatch.setattr(pwd, "getpwall", lambda: [me, *others])


@pytest.mark.parametrize(
    ("group", "members", "primary", "private"),
    [
        ("me", [], [], True),
        ("me", ["me"], [], True),
        ("me", ["me", "dev"], [], False),
        ("me", [], [1001], False),
        ("staff", [], [], False),
    ],
    ids=["own", "own-listed", "another-member", "another-primary", "shared-name"],
)
def test_group_write_is_trusted_only_for_your_private_group(
    monkeypatch: pytest.MonkeyPatch,
    group: str,
    members: list[str],
    primary: list[int],
    private: bool,
) -> None:
    _accounts(monkeypatch, group, members, primary)
    assert folder_layer_module._private_group(1000, 1000) is private


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX permissions")
def test_a_chmod_alone_makes_the_file_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, "demo: {org: Platform}\n")
    file = root / ".untaped" / "config.yml"
    file.chmod(0o666)
    assert _demo_now().org == "home-org"
    file.chmod(0o600)  # same content, size and mtime: only the inode's ctime moves
    assert _demo_now().org == "Platform"


def test_a_moved_project_s_file_is_still_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, monkeypatch, "demo: {org: Platform}\n")
    moved = root.rename(tmp_path / "PROJ-123-moved")
    monkeypatch.chdir(moved)
    forget_folder_layer()
    assert _demo_now().org == "Platform"


def test_a_run_that_leaves_the_folder_out_reads_no_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.runtime.run import child_run

    _project(tmp_path, monkeypatch, "demo: {org: Platform}\n")
    with entered(start_run()):
        assert folder_layer() is not None
        with child_run(folder=False):
            assert folder_layer() is None
            assert load_settings_section("demo").org == "home-org"
