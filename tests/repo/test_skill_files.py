"""Packaged skills are self-contained manuals that match the installed CLI.

An agent that has only the installed ``untaped`` reads these files, so:

- every ``untaped …`` command quoted in a skill file (``bash`` blocks and
  inline code spans in ``SKILL.md`` and ``references/``, comment lines of
  ``examples/*.yml``) and in the comments of the ``awx test init`` starter
  suite must parse against the real command tree: cyclopts resolves the
  command and binds its arguments, and nothing runs. Synopsis notation is
  read the obvious way (``[--flag X]`` is optional text, ``a|b`` takes
  ``a``, ``...`` repeats), and placeholders (``NAME``, ``OWNER/NAME``,
  ``<recipe>``) bind to their parameter without being converted; values are
  not validated (paths need not exist). Only a command named in inline code
  may leave out required arguments: a full command line may not;
- no skill file points at the source repository (``docs/…``, "the untaped
  repository"), which does not exist next to an installed CLI;
- each skill's ``SPEC`` description matches its ``SKILL.md`` frontmatter, so
  ``skills list`` shows what the agent's skill loader sees;
- each description routes rather than instructs: it is read in every session
  next to every other skill's, so it stays under 60 words and is written in
  the third person (``Operates …``, never ``Use the …``).
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

from untaped.bootstrap import build_root_app
from untaped.capabilities.registry import CapabilitySpec, ProviderCandidate
from untaped.sdk import SkillAsset
from untaped_awx.domain.suite_starter import starter_suite

SKILL_NAMES = tuple(
    f"untaped-{name}" for name in ("ansible", "awx", "github", "jira", "recipe", "workspace")
)


@pytest.fixture(scope="module")
def skills(first_party_specs: tuple[CapabilitySpec, ...]) -> dict[str, SkillAsset]:
    """Every first-party skill by name."""
    return {skill.name: skill for spec in first_party_specs for skill in spec.skills}


def _skill_files(skill: SkillAsset) -> list[Path]:
    return sorted(skill.source.rglob("*.md"))


def _example_files(skill: SkillAsset) -> list[Path]:
    return sorted(skill.source.rglob("*.yml"))


def test_the_skills_are_the_first_party_capabilities_skills(skills: dict[str, SkillAsset]) -> None:
    assert tuple(sorted(skills)) == SKILL_NAMES


_FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)
_INLINE = re.compile(r"`(untaped(?: [^`]*)?)`")
_SEGMENT_BREAK = re.compile(r"\s+\|\s+|\s*(?:&&|\|\||;)\s*")
_REDIRECT = re.compile(r"^\d*[<>]")
_SYNOPSIS_SUFFIX = re.compile(r"(?:\]|\.\.\.|…)+$")
_PLACEHOLDER = re.compile(r"^[A-Z][A-Z0-9_]*(?:[/=:.-][A-Z0-9_]+)*$")
_ROOT_FLAGS = {"-q", "--quiet", "-v", "--verbose"}
_HELP_FLAGS = {"--help", "-h"}


def _commands(text: str) -> Iterator[tuple[str, bool]]:
    """Every ``untaped …`` command in Markdown, and whether it is inline code.

    Commands in ``bash`` blocks come first, then those in inline code spans.
    """
    for language, block in _FENCE.findall(text):
        if language in {"bash", "sh", "shell"}:
            for line in block.replace("\\\n", " ").splitlines():
                yield from ((command, False) for command in _segments(line.split(" #", 1)[0]))
    for span in _INLINE.findall(_FENCE.sub("", text)):
        yield from ((command, True) for command in _segments(span))


def _comment_commands(text: str) -> Iterator[tuple[str, bool]]:
    """Every ``untaped …`` command in YAML comment lines (inline code, or the whole line)."""
    for line in text.splitlines():
        comment = line.lstrip()
        if not comment.startswith("#"):
            continue
        spans = _INLINE.findall(comment)
        if spans:
            yield from ((command, True) for span in spans for command in _segments(span))
        else:
            yield from ((command, False) for command in _segments(comment.lstrip("# ")))


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


def _parse_problem(root: App, command: str, *, inline: bool) -> str | None:
    """Why ``command`` does not parse against ``root`` (``None`` when it does).

    ``inline``: a command named in prose, which may leave out required arguments.
    """
    argv, placeholders = _argv(command)
    if not argv:
        return None
    if _HELP_FLAGS & set(argv):
        _, _, unused = root.parse_commands([token for token in argv if token not in _HELP_FLAGS])
        return f"not a command: {' '.join(unused)}" if unused else None
    try:
        root.parse_args(argv, exit_on_error=False, print_error=False, help_on_error=False)
    except ValidationError:
        return None
    except MissingArgumentError as exc:
        return None if inline else str(exc)
    except CoercionError as exc:
        if exc.token is not None and exc.token.value in placeholders:
            return None
        return str(exc)
    except CycloptsError as exc:
        return str(exc)
    return None


@pytest.fixture(scope="module")
def root(first_party_candidates: tuple[ProviderCandidate, ...]) -> App:
    return build_root_app(candidates=first_party_candidates)


def _problems(root: App, commands: Iterator[tuple[str, bool]]) -> list[str]:
    return [
        f"{command}: {problem}"
        for command, inline in dict.fromkeys(commands)
        if (problem := _parse_problem(root, command, inline=inline)) is not None
    ]


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_every_quoted_command_parses_against_the_cli(
    root: App, skills: dict[str, SkillAsset], name: str
) -> None:
    skill = skills[name]
    problems: list[str] = []
    for path in [*_skill_files(skill), *_example_files(skill)]:
        text = path.read_text(encoding="utf-8")
        commands = _commands(text) if path.suffix == ".md" else _comment_commands(text)
        problems += [f"{path.relative_to(skill.source)}: {p}" for p in _problems(root, commands)]

    assert problems == []


def test_the_starter_suite_comments_name_real_commands(root: App) -> None:
    text = starter_suite("Deploy", organization="Default", launch={}, survey=[])
    commands = list(_comment_commands(text))

    assert commands
    assert _problems(root, iter(commands)) == []


_REPO_ONLY = re.compile(r"(?<![\w.~/-])docs/|untaped repository|\bsrc/untaped\b|CONTRIBUTING\.md")


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_files_do_not_point_into_the_source_repository(
    skills: dict[str, SkillAsset], name: str
) -> None:
    skill = skills[name]
    offending = [
        f"{path.relative_to(skill.source)}: {line}"
        for path in [*_skill_files(skill), *_example_files(skill)]
        for line in path.read_text(encoding="utf-8").splitlines()
        if _REPO_ONLY.search(line)
    ]

    assert offending == []


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_relative_links_stay_inside_the_skill(skills: dict[str, SkillAsset], name: str) -> None:
    source = skills[name].source.resolve()
    broken = [
        f"{path.relative_to(source)}: {target}"
        for path in _skill_files(skills[name])
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", path.read_text(encoding="utf-8"))
        if not re.match(r"^[a-z][a-z0-9+.-]*:", target)
        and not (
            (path.parent / target).resolve().is_relative_to(source)
            and (path.parent / target).exists()
        )
    ]

    assert broken == []


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_spec_description_matches_the_skill_frontmatter(
    skills: dict[str, SkillAsset], name: str
) -> None:
    text = skills[name].source.joinpath("SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])

    assert frontmatter["name"] == name
    assert frontmatter["description"] == skills[name].description


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_description_is_a_short_third_person_router(
    skills: dict[str, SkillAsset], name: str
) -> None:
    description = skills[name].description

    assert len(description.split()) < 60, "keep the description under 60 words"
    assert re.match(r"[A-Z][a-z]+s\b", description), (
        "open with a third-person verb ('Operates …'), not an instruction"
    )
    assert not re.search(r"\b(you|your)\b", description, re.IGNORECASE), (
        "a description describes the skill; it does not address the reader"
    )
