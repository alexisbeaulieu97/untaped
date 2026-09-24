"""Documentation checks: generated config reference, links and command examples.

- ``docs/reference/config.md`` must match ``scripts/gen_config_reference.py``
  output, and every setting must have a description.
- Every relative Markdown link (and ``#anchor``) in ``docs/``, ``README.md``,
  ``AGENTS.md`` and ``CONTRIBUTING.md`` must resolve.
- Every ``untaped`` example in a ``bash`` block must name a real command and
  only options that command accepts.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import sys
from pathlib import Path
from types import ModuleType

import pytest
from cyclopts import App

from untaped.bootstrap import build_root_app

REPO_ROOT = Path(__file__).resolve().parents[2]
REGENERATE = "uv run python scripts/gen_config_reference.py"

_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
_FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.MULTILINE)


def _generator() -> ModuleType:
    path = REPO_ROOT / "scripts" / "gen_config_reference.py"
    spec = importlib.util.spec_from_file_location("gen_config_reference", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_config_reference_is_current() -> None:
    generator = _generator()
    page = (REPO_ROOT / "docs" / "reference" / "config.md").read_text(encoding="utf-8")
    assert page == generator.render(), f"docs/reference/config.md is stale; run: {REGENERATE}"


def test_every_setting_has_a_description() -> None:
    generator = _generator()
    assert generator.missing_descriptions() == [], (
        "add Field(description=...) or a DESCRIPTIONS entry in scripts/gen_config_reference.py"
    )
    assert generator.unknown_descriptions() == [], "DESCRIPTIONS names a setting that is gone"


def _markdown_files() -> list[Path]:
    files = sorted((REPO_ROOT / "docs").rglob("*.md"))
    return [*files, *(REPO_ROOT / name for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md"))]


def _slug(heading: str) -> str:
    text = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    return {_slug(match) for match in _HEADING.findall(text)}


def _broken_links(path: Path) -> list[str]:
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    text = re.sub(r"`[^`\n]*`", "", text)
    broken: list[str] = []
    for target in _LINK.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):
            continue
        ref, _, anchor = target.partition("#")
        resolved = (path.parent / ref).resolve() if ref else path
        checks_anchor = bool(anchor) and resolved.suffix == ".md"
        if not resolved.exists() or (checks_anchor and anchor not in _anchors(resolved)):
            broken.append(target)
    return broken


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_relative_links_resolve(path: Path) -> None:
    assert _broken_links(path) == []


_ROOT_OPTIONS = {"--profile", "--verbose", "-v", "--quiet", "-q", "--help", "-h"}
_ROOT_ONLY_OPTIONS = _ROOT_OPTIONS | {"--version", "--install-completion"}
_EXAMPLE_PROVIDER = "acme"
"""The example external capability in docs/plugins.md."""


def _bash_blocks(path: Path) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if current is None and line.strip() == "```bash":
            current = []
        elif current is not None and line.strip() == "```":
            blocks.append("\n".join(current))
            current = None
        elif current is not None:
            current.append(line)
    return blocks


def _untaped_commands(block: str) -> list[list[str]]:
    """Every ``untaped ...`` command in a bash block, as argv without ``untaped``."""
    commands: list[list[str]] = []
    for line in block.replace("\\\n", " ").splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars="|;&<>()")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        segment: list[str] = []
        for token in [*lexer, "|"]:
            if token and set(token) <= set("|;&<>()"):
                if segment[:1] == ["untaped"]:
                    commands.append(segment[1:])
                segment = []
            else:
                segment.append(token)
    return commands


def _unknown_options(root: App, argv: list[str]) -> list[str]:
    app, path, flags, words = root, [], [], []
    skip_value = False
    for token in argv:
        if skip_value:
            skip_value = False
        elif token.startswith("-"):
            flags.append(token.split("=")[0])
            skip_value = token == "--profile"
        elif token in app:
            app = app[token]
            path.append(token)
        else:
            words.append(token)
    if not path:
        if words[:1] == [_EXAMPLE_PROVIDER]:
            return []
        if words:
            return [f"untaped {words[0]}: not a command"]
        return [f"untaped: {flag}" for flag in flags if flag not in _ROOT_ONLY_OPTIONS]
    if app.default_command is None:
        return [] if "--help" in flags else [f"{' '.join(path) or 'untaped'}: not a command"]
    names = set(_ROOT_OPTIONS)
    for argument in app.assemble_argument_collection(parse_docstring=True):
        names.update(argument.names)
    return [f"{' '.join(path)}: {flag}" for flag in flags if flag not in names]


def test_command_examples_use_real_commands_and_options() -> None:
    """Every ``untaped`` example in a ``bash`` block names a real command and options."""
    root = build_root_app(externals=[])
    problems = []
    for path in _markdown_files():
        if "templates" in path.parts:
            continue
        for block in _bash_blocks(path):
            for argv in _untaped_commands(block):
                problems.extend(
                    f"{path.relative_to(REPO_ROOT)}: {problem}"
                    for problem in _unknown_options(root, argv)
                )
    assert problems == []
