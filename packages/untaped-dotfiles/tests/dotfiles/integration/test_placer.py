"""FilesystemPlacer against a real temporary filesystem."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped_dotfiles.domain.hashing import content_hash
from untaped_dotfiles.domain.status import TargetInfo
from untaped_dotfiles.infrastructure import FilesystemPlacer

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def placer(tmp_path: Path) -> FilesystemPlacer:
    return FilesystemPlacer(home=tmp_path / "home", kept_dir=tmp_path / "kept", now=lambda: T0)


def test_observe_distinguishes_missing_file_dir_and_symlink(
    placer: FilesystemPlacer, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    assert placer.observe(home / "nope") == TargetInfo("missing")
    (home / "f").write_text("x")
    assert placer.observe(home / "f") == TargetInfo("file", hash=content_hash(b"x"))
    (home / "d").mkdir()
    assert placer.observe(home / "d") == TargetInfo("dir")
    os.symlink(home / "f", home / "l")
    assert placer.observe(home / "l") == TargetInfo("symlink", link_to=str(home / "f"))
    os.symlink(home / "gone", home / "dangling")
    assert placer.observe(home / "dangling").kind == "symlink"


def test_observe_hashes_the_managed_keys_of_a_merge_target(
    placer: FilesystemPlacer, tmp_path: Path
) -> None:
    target = tmp_path / "home" / "s.json"
    target.parent.mkdir()
    target.write_text('{"a": {"b": 1}, "c": 2}')
    one = placer.observe(target, fmt="json", managed=(("a", "b"),))
    target.write_text('{"a": {"b": 1}, "c": 3}')
    assert placer.observe(target, fmt="json", managed=(("a", "b"),)) == one
    target.write_text('{"a": {"b": 9}, "c": 3}')
    assert placer.observe(target, fmt="json", managed=(("a", "b"),)) != one
    target.write_text("not json")
    assert placer.observe(target, fmt="json").kind == "other"


def test_keep_aside_moves_into_the_kept_dir_by_repo_item_and_time(
    placer: FilesystemPlacer, tmp_path: Path
) -> None:
    target = tmp_path / "home" / ".config" / "fish" / "config.fish"
    target.parent.mkdir(parents=True)
    target.write_text("mine")
    kept = placer.keep_aside(target, repo="dotfiles", item="fish")
    assert kept == tmp_path / "kept/dotfiles/fish/20261002T120000Z/.config/fish/config.fish"
    assert kept.read_text() == "mine" and not target.exists()
    outside = tmp_path / "etc" / "x.conf"
    outside.parent.mkdir()
    outside.write_text("x")
    copied = placer.keep_aside(outside, repo="r", item="i", copy=True)
    assert copied.relative_to(tmp_path / "kept").as_posix().endswith("/etc/x.conf")
    assert outside.exists()


def test_link_copy_and_delete(placer: FilesystemPlacer, tmp_path: Path) -> None:
    source = tmp_path / "repo" / "a"
    source.parent.mkdir()
    source.write_text("content")
    target = tmp_path / "home" / "deep" / "a"
    assert placer.link(source, target) == str(source)
    assert os.readlink(target) == str(source)
    placer.link(source, target)  # relinking replaces the link in place
    assert placer.copy(b"#!/bin/sh\n", target, executable=True) == content_hash(b"#!/bin/sh\n")
    assert not target.is_symlink() and os.access(target, os.X_OK)
    placer.copy(b"plain", target, executable=False)
    assert not os.access(target, os.X_OK) and target.read_bytes() == b"plain"
    placer.delete(target)
    assert not target.exists()
    placer.delete(target)  # idempotent


def test_copy_honours_the_umask(placer: FilesystemPlacer, tmp_path: Path) -> None:
    target = tmp_path / "home" / "private"
    before = os.umask(0o077)
    try:
        placer.copy(b"token=1\n", target, executable=False)
        assert target.stat().st_mode & 0o777 == 0o600
        placer.copy(b"#!/bin/sh\n", target, executable=True)
        assert target.stat().st_mode & 0o777 == 0o700
    finally:
        os.umask(before)


def test_render_reads_text_and_marks_binary(placer: FilesystemPlacer, tmp_path: Path) -> None:
    target = tmp_path / "home" / "t"
    target.parent.mkdir()
    assert placer.render(target) is None
    target.write_text("hi\n")
    assert placer.render(target) == "hi\n"
    target.write_bytes(b"\xff\xfe\x00")
    assert placer.render(target) == "<binary>\n"
