"""Packaged skills are self-contained manuals that match the installed CLI.

An agent that has only the installed ``untaped`` reads these files, so:

- every ``untaped …`` command quoted in a skill file (``bash`` blocks and
  inline code spans, in ``SKILL.md`` and ``references/``) must parse against
  the real command tree: cyclopts resolves the command and binds its
  arguments, and nothing runs. Synopsis notation is read the obvious way
  (``[--flag X]`` is optional text, ``a|b`` takes ``a``, ``...`` repeats),
  and placeholders (``NAME``, ``OWNER/NAME``, ``<recipe>``) bind to their
  parameter without being converted; values are not validated (paths need
  not exist) and a command named in prose may leave out required arguments;
- no skill file points at the source repository (``docs/…``, "the untaped
  repository"), which does not exist next to an installed CLI;
- each skill's ``SPEC`` description matches its ``SKILL.md`` frontmatter, so
  ``skills list`` shows what the agent's skill loader sees.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from cyclopts import App
from cyclopts.exceptions import (
    CoercionError,
    CycloptsError,
    MissingArgumentError,
    ValidationError,
)

from untaped import bootstrap
from untaped.bootstrap import build_root_app

SKILLS = {skill.name: skill for spec in bootstrap.BUILTIN_CAPABILITIES for skill in spec.skills}


def _skill_files() -> list[tuple[str, Path]]:
    return [
        (name, path)
        for name, skill in sorted(SKILLS.items())
        for path in sorted(skill.source.rglob("*.md"))
    ]


_FILES = _skill_files()
_IDS = [f"{name}/{path.relative_to(SKILLS[name].source)}" for name, path in _FILES]

_FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)
_INLINE = re.compile(r"`(untaped(?: [^`]*)?)`")
_SEGMENT_BREAK = re.compile(r"\s+\|\s+|\s*(?:&&|\|\||;)\s*")
_REDIRECT = re.compile(r"^\d*[<>]")
_SYNOPSIS_SUFFIX = re.compile(r"(?:\]|\.\.\.|…)+$")
_PLACEHOLDER = re.compile(r"^[A-Z][A-Z0-9_]*(?:[/=:.-][A-Z0-9_]+)*$")
_ROOT_FLAGS = {"-q", "--quiet", "-v", "--verbose"}
_HELP_FLAGS = {"--help", "-h"}


def _commands(text: str) -> Iterator[str]:
    """Every ``untaped …`` command line: in ``bash`` blocks, then in inline code."""
    for language, block in _FENCE.findall(text):
        if language in {"bash", "sh", "shell"}:
            for line in block.replace("\\\n", " ").splitlines():
                yield from _segments(line.split(" #", 1)[0])
    for span in _INLINE.findall(_FENCE.sub("", text)):
        yield from _segments(span)


def _segments(line: str) -> Iterator[str]:
    """The ``untaped`` commands of a shell line (pipes and ``&&``/``;`` lists split)."""
    for segment in _SEGMENT_BREAK.split(line.strip()):
        words = segment.split()
        while words and re.match(r"^[A-Z_]+=\S*$", words[0]):
            words.pop(0)  # an environment assignment
        if words[:1] == ["untaped"]:
            yield " ".join(words)


def _argv(command: str) -> tuple[list[str], set[str]]:
    """``command`` as argv (without ``untaped``) plus the placeholder tokens in it."""
    argv: list[str] = []
    placeholders: set[str] = set()
    tokens = shlex.split(command)[1:]
    skip = False
    for raw in tokens:
        if skip:
            skip = False
            continue
        if _REDIRECT.match(raw):
            skip = raw.rstrip("0123456789&") in {">", "<", ">>", "2>"}
            continue
        if raw in _ROOT_FLAGS:
            continue
        if raw == "--profile":
            skip = True
            continue
        token = _SYNOPSIS_SUFFIX.sub("", raw.removeprefix("["))
        token = token.split("|", 1)[0] if "|" in token and not token.startswith("{") else token
        if "<" in token:
            token = token.replace("<", "").replace(">", "")
            placeholders.add(token)
        if not token:
            continue
        if _PLACEHOLDER.match(token.split("=", 1)[-1]):
            placeholders.add(token)
        argv.append(token)
    return argv, placeholders


def _parse_problem(root: App, command: str) -> str | None:
    """Why ``command`` does not parse against ``root`` (``None`` when it does)."""
    argv, placeholders = _argv(command)
    if not argv:
        return None
    if _HELP_FLAGS & set(argv):
        _, _, unused = root.parse_commands([token for token in argv if token not in _HELP_FLAGS])
        return f"not a command: {' '.join(unused)}" if unused else None
    try:
        root.parse_args(argv, exit_on_error=False, print_error=False, help_on_error=False)
    except ValidationError, MissingArgumentError:
        return None
    except CoercionError as exc:
        if exc.token is not None and exc.token.value in placeholders:
            return None
        return str(exc)
    except CycloptsError as exc:
        return str(exc)
    return None


@pytest.fixture(scope="module")
def root() -> App:
    return build_root_app(externals=[])


@pytest.mark.parametrize(("name", "path"), _FILES, ids=_IDS)
def test_every_quoted_command_parses_against_the_cli(root: App, name: str, path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    problems = [
        f"{command}: {problem}"
        for command in dict.fromkeys(_commands(text))
        if (problem := _parse_problem(root, command)) is not None
    ]
    assert problems == []


_REPO_ONLY = re.compile(r"(?<![\w.~/-])docs/|untaped repository|\bsrc/untaped\b|CONTRIBUTING\.md")


@pytest.mark.parametrize(("name", "path"), _FILES, ids=_IDS)
def test_skill_files_do_not_point_into_the_source_repository(name: str, path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()

    offending = [line for line in lines if _REPO_ONLY.search(line)]

    assert offending == []


@pytest.mark.parametrize(("name", "path"), _FILES, ids=_IDS)
def test_relative_links_stay_inside_the_skill(name: str, path: Path) -> None:
    source = SKILLS[name].source.resolve()
    targets = re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", path.read_text(encoding="utf-8"))

    broken = [
        target
        for target in targets
        if not re.match(r"^[a-z][a-z0-9+.-]*:", target)
        and not (
            (path.parent / target).resolve().is_relative_to(source)
            and (path.parent / target).exists()
        )
    ]

    assert broken == []


@pytest.mark.parametrize("name", sorted(SKILLS))
def test_spec_description_matches_the_skill_frontmatter(name: str) -> None:
    text = SKILLS[name].source.joinpath("SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])

    assert frontmatter["name"] == name
    assert frontmatter["description"] == SKILLS[name].description
