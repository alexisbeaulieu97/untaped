"""Real-git fixtures: a source served at an ``https://`` URL, and isolated dotfiles folders."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from dotfiles_test_support import (
    FILES,
    PACKAGES,
    forget_installed,
    package_files,
    publish,
    write_files,
)
from untaped_git.testing import GitRemote, git_remote


@pytest.fixture(autouse=True)
def _one_install() -> Iterator[None]:
    forget_installed()
    yield
    forget_installed()


@pytest.fixture
def make_remote(tmp_path: Path) -> Callable[..., GitRemote]:
    """A git source with packages and files, served at ``https://git.example/<name>.git``.

    Its authoring clone (``remote.work``) is where a test commits new versions
    (``publish``).
    """
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    def make(
        name: str = "dotfiles",
        *,
        packages: dict[str, str] | None = None,
        files: dict[str, str] | None = None,
    ) -> GitRemote:
        remote = git_remote(tmp_path / "remotes", name)
        chosen = PACKAGES if packages is None else packages
        publish(remote, {**package_files(chosen), **(FILES if files is None else files)}, "init")
        return remote

    return make


@pytest.fixture
def make_folder(tmp_path: Path) -> Callable[..., Path]:
    """A local folder source with packages and files."""

    def make(
        name: str = "local",
        *,
        packages: dict[str, str] | None = None,
        files: dict[str, str] | None = None,
    ) -> Path:
        folder = tmp_path / "folders" / name
        chosen = PACKAGES if packages is None else packages
        write_files(folder, {**package_files(chosen), **(FILES if files is None else files)})
        return folder

    return make


@pytest.fixture
def user_home() -> Path:
    """The hermetic ``HOME`` of this test."""
    return Path.home()


@pytest.fixture
def dotfiles_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the dotfiles folders at tmp, fix the OS to linux; return the state folder."""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("UNTAPED_DOTFILES__CLONES_DIR", str(tmp_path / "clones"))
    monkeypatch.setenv("UNTAPED_DOTFILES__KEPT_DIR", str(tmp_path / "kept"))
    monkeypatch.setenv("UNTAPED_DOTFILES__STATE_DIR", str(state_dir))
    monkeypatch.setenv("UNTAPED_DOTFILES__OS", "linux")
    yield state_dir
