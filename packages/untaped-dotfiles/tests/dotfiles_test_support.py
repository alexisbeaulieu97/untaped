"""What the dotfiles tests share: a manifest, git helpers, and one run of the plugin."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from untaped.sdk.testing import Ran, ScriptedTerminal, install
from untaped_dotfiles.plugin import Dotfiles

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


@dataclass(frozen=True)
class Result:
    """What one ``untaped dotfiles`` run did, its stderr as one text."""

    exit_code: int
    stdout: str
    stderr: str
    ran: Ran

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


def run(args: Sequence[str], *, input: str | None = None) -> Result:
    """Run ``untaped dotfiles ARGS`` in the test's home, with the ``terminal`` fixture's answers."""
    ran = install(Dotfiles).cli(["dotfiles", *args], input=input)
    return Result(int(ran.exit), ran.stdout, "".join(f"{line}\n" for line in ran.stderr), ran)


#: ``make_upstream(name=..., manifest=..., files=...)``: ``(bare, author checkout)``.
Upstream = Callable[..., tuple[Path, Path]]


def json_rows(result: Result) -> list[dict[str, object]]:
    assert result.stdout.strip(), result.output
    rows: list[dict[str, object]] = json.loads(result.stdout)
    return rows


def as_json(args: list[str]) -> list[dict[str, object]]:
    result = run([*args, "--format", "json"])
    assert result.exit_code in (0, 3), result.output
    return json_rows(result)


def states(args: list[str] | None = None) -> dict[str, str]:
    found = as_json(["status", *(args or [])])
    return {str(row["source"]): str(row["state"]) for row in found}


def subscribe(bare: Path, *extra: str) -> Result:
    result = run(["subscribe", str(bare), *extra, "--format", "json"])
    assert result.exit_code == 0, result.output
    return result


def answered(terminal: ScriptedTerminal, answer: bool, args: list[str]) -> Result:
    """Run with a terminal that answers the question ``answer``, then take it away."""
    terminal.answers(answer)
    try:
        return run(args)
    finally:
        terminal.absent()


def bump(author: Path, files: dict[str, str], message: str = "update") -> None:
    write_files(author, files)
    commit_all(author, message)
    push(author)
