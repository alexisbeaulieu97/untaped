"""Real-git fixtures: a throwaway dotfiles repo with a manifest, and isolated dotfiles dirs."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from untaped.settings import get_settings

MANIFEST = """\
version: 1
items:
  fish:
    description: fish shell config
    policy: manual
    files:
      - source: fish/config.fish
        target: ~/.config/fish/config.fish
        mode: link
      - source: fish/conf.d
        target: ~/.config/fish/conf.d
        mode: link
  starship:
    policy: sync
    files:
      - source: starship.toml
        target: ~/.config/starship.toml
        mode: copy
  claude:
    policy: sync
    files:
      - name: settings
        source: claude/settings.json
        target: ~/.claude/settings.json
        mode: merge
        unless: [claude-plugin-dev]
      - source: claude/skills
        target: ~/.agents/skills/mine
        mode: link
      - source: claude/skills
        target: ~/.claude/skills/mine
        mode: link
  mac-only:
    os: [macos]
    files:
      - source: mac.txt
        target: ~/mac.txt
        mode: copy
"""

FILES = {
    "fish/config.fish": "set -gx EDITOR vim\n",
    "fish/conf.d/abbr.fish": "abbr g git\n",
    "fish/conf.d/path.fish": "fish_add_path ~/bin\n",
    "starship.toml": "[character]\nsuccess_symbol = '>'\n",
    "claude/settings.json": '{"enabledPlugins": {"mine": true}, "theme": "dark"}\n',
    "claude/skills/hello/SKILL.md": "# hello\n",
    "mac.txt": "mac\n",
}


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _configure(repo: Path) -> None:
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(repo, "config", key, value)


def write_files(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def commit_all(repo: Path, message: str = "change") -> str:
    _configure(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def push(repo: Path) -> None:
    git(repo, "push", "-q", "origin", "HEAD")


@pytest.fixture
def make_upstream(tmp_path: Path) -> Callable[..., tuple[Path, Path]]:
    """Create a bare upstream with a manifest and files; return ``(bare, author checkout)``.

    The author checkout is where a test commits new versions and pushes them.
    """
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    def make(
        name: str = "dotfiles",
        *,
        manifest: str = MANIFEST,
        files: dict[str, str] | None = None,
    ) -> tuple[Path, Path]:
        bare = tmp_path / "remotes" / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(bare))
        author = tmp_path / "author" / name
        author.parent.mkdir(parents=True, exist_ok=True)
        git(tmp_path, "clone", "-q", str(bare), str(author))
        write_files(author, {"dotfiles.yml": manifest, **(FILES if files is None else files)})
        commit_all(author, "init")
        push(author)
        return bare, author

    return make


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """The hermetic ``HOME`` of this test."""
    return Path.home()


@pytest.fixture
def dotfiles_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the dotfiles dirs at tmp, fix the OS to linux; return the state dir."""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("UNTAPED_DOTFILES__REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("UNTAPED_DOTFILES__KEPT_DIR", str(tmp_path / "kept"))
    monkeypatch.setenv("UNTAPED_DOTFILES__STATE_DIR", str(state_dir))
    monkeypatch.setenv("UNTAPED_DOTFILES__OS", "linux")
    get_settings.cache_clear()
    yield state_dir
    get_settings.cache_clear()
