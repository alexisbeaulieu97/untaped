"""What the dotfiles tests share: a source's packages and files, and one run of the plugin."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from untaped.sdk.testing import Install, Ran, ScriptedTerminal, install
from untaped_dotfiles.plugin import Dotfiles
from untaped_git.testing import GitRemote

#: ``make_remote(name=..., packages=..., files=...)``: a source at ``https://git.example/<name>.git``.
type MakeRemote = Callable[..., GitRemote]
#: ``make_folder(name=..., packages=..., files=...)``: a local folder source.
type MakeFolder = Callable[..., Path]

FISH = """\
version: 1
description: fish shell config
policy: manual
items:
  config:
    files:
      - {path: fish/config.fish, target: ~/.config/fish/config.fish}
      - {path: fish/conf.d, target: ~/.config/fish/conf.d}
"""

STARSHIP = """\
version: 1
description: Starship prompt
policy: sync
items:
  config:
    files:
      - {path: starship.toml, target: ~/.config/starship.toml, mode: copy}
"""

CLAUDE = """\
version: 1
description: Claude Code settings and skills
policy: sync
inputs:
  claude_dir: {description: Claude Code's folder, default: "~/.claude"}
items:
  settings:
    unless: [claude-plugin-dev]
    files:
      - {path: claude/settings.json, target: "{{ claude_dir }}/settings.json", mode: merge}
  skills:
    files:
      - {path: claude/skills, target: ~/.agents/skills/mine}
      - {path: claude/skills, target: "{{ claude_dir }}/skills/mine"}
"""

MAC = """\
version: 1
description: macOS only
os: [macos]
items:
  defaults:
    files:
      - {path: mac.txt, target: ~/mac.txt, mode: copy}
"""

GIT = """\
version: 1
description: Global gitignore and identity
policy: sync
inputs:
  git_email: {description: Email on commits}
  git_profile: {description: Which identity file}
items:
  ignore:
    files:
      - {path: git/ignore, target: ~/.config/git/ignore}
  identity:
    files:
      - {path: git/identity, target: "~/.config/git/{{ git_profile }}.inc"}
    steps:
      - name: user-email
        check: {command: [git, config, --global, user.email], prints: "{{ git_email }}"}
        run: [git, config, --global, user.email, "{{ git_email }}"]
"""

PACKAGES = {"fish": FISH, "starship": STARSHIP, "claude": CLAUDE, "mac": MAC, "git": GIT}

FILES = {
    "fish/config.fish": "set -gx EDITOR vim\n",
    "fish/conf.d/abbr.fish": "abbr g git\n",
    "fish/conf.d/path.fish": "fish_add_path ~/bin\n",
    "starship.toml": "[character]\nsuccess_symbol = '>'\n",
    "claude/settings.json": '{"enabledPlugins": {"mine": true}, "theme": "dark"}\n',
    "claude/skills/hello/SKILL.md": "# hello\n",
    "mac.txt": "mac\n",
    "git/ignore": ".DS_Store\n",
    "git/identity": "identity\n",
}


def package_files(packages: dict[str, str]) -> dict[str, str]:
    """``packages`` as the files of a source: ``.untaped/dotfiles/<name>.yml``."""
    return {f".untaped/dotfiles/{name}.yml": text for name, text in packages.items()}


#: Who the tests' commits are by.
_IDENTITY = (
    "-c",
    "user.name=Test",
    "-c",
    "user.email=test@example.invalid",
    "-c",
    "commit.gpgsign=false",
)


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def write_files(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def publish(remote: GitRemote, files: dict[str, str], message: str = "update") -> str:
    """Write ``files`` in the remote's authoring clone, commit and push them; the commit."""
    write_files(remote.work, files)
    git(remote.work, "add", "-A")
    git(remote.work, *_IDENTITY, "commit", "-q", "-m", message)
    git(remote.work, "push", "-q", "origin", "HEAD:main")
    return git(remote.work, "rev-parse", "HEAD")


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


#: The test's one composition: ``install`` writes each plugin's section afresh, so a test
#: installs once and every run keeps what the ones before wrote to ``config.yml``.
_INSTALLED: list[Install] = []


def installed(providers: Mapping[str, Sequence[type]] | None = None) -> Install:
    """The test's composition of dotfiles (with ``providers`` the first time it is asked)."""
    if not _INSTALLED:
        _INSTALLED.append(install(Dotfiles, providers=providers))
    return _INSTALLED[0]


def forget_installed() -> None:
    _INSTALLED.clear()


def run(args: Sequence[str], *, input: str | None = None) -> Result:
    """Run ``untaped dotfiles ARGS`` in the test's home, with the ``terminal`` fixture's answers."""
    ran = installed().cli(["dotfiles", *args], input=input)
    return Result(int(ran.exit), ran.stdout, "".join(f"{line}\n" for line in ran.stderr), ran)


def json_rows(result: Result) -> list[dict[str, object]]:
    assert result.stdout.strip(), result.output
    rows: list[dict[str, object]] = json.loads(result.stdout)
    return rows


def as_json(args: list[str]) -> list[dict[str, object]]:
    result = run([*args, "--format", "json"])
    assert result.exit_code in (0, 3), result.output
    return json_rows(result)


def states(args: list[str] | None = None) -> dict[str, str]:
    """``entry → state`` of ``status``."""
    found = as_json(["status", *(args or [])])
    return {str(row["entry"]): str(row["state"]) for row in found}


def subscribe(source: str, *extra: str) -> Result:
    result = run(["subscribe", source, *extra, "--format", "json"])
    assert result.exit_code == 0, result.output
    return result


def answered(terminal: ScriptedTerminal, answer: bool, args: list[str]) -> Result:
    """Run with a terminal that answers the question ``answer``, then take it away."""
    terminal.answers(answer)
    try:
        return run(args)
    finally:
        terminal.absent()
