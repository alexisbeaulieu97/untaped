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
- relative links in a skill file stay inside the skill;
- each skill's ``SPEC`` description matches its ``SKILL.md`` frontmatter, so
  ``skills list`` shows what the agent's skill loader sees;
- every ``SKILL.md`` has the sections of the skill template in its order
  (others may sit between them), so an agent finds the same thing in the same
  place in every skill (the example plugin's skill too, which has no
  references);
- ``SKILL.md`` stays within :data:`SKILL_BUDGET` lines and each reference
  within :data:`REFERENCE_BUDGET`; an over-budget file is split by task.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from cyclopts import App

from repo import quoted_commands
from repo.support import FENCE, FIRST_PARTY, REPO_ROOT
from untaped.bootstrap import build_root_app
from untaped.capabilities.registry import CapabilitySpec, ProviderCandidate
from untaped.sdk import SkillAsset
from untaped_awx.domain.suite_starter import starter_suite

SKILL_NAMES = tuple(f"untaped-{name}" for name in FIRST_PARTY)


#: The skill template's sections (docs/plugins.md), in order.
SKILL_SECTIONS = ("Setup", "Commands", "Workflows", "Safety", "Pitfalls", "References")
SKILL_BUDGET = 500
REFERENCE_BUDGET = 300
EXAMPLE_SKILL = REPO_ROOT / "examples/untaped-hello/src/untaped_hello/skills/untaped-hello/SKILL.md"


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


@pytest.fixture(scope="module")
def root(first_party_candidates: tuple[ProviderCandidate, ...]) -> App:
    return build_root_app(candidates=first_party_candidates)


def _problems(root: App, commands: Iterator[tuple[str, bool]]) -> list[str]:
    return [
        f"{command}: {problem}"
        for command, inline in dict.fromkeys(commands)
        if (problem := quoted_commands.parse_problem(root, command, inline=inline)) is not None
    ]


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_every_quoted_command_parses_against_the_cli(
    root: App, skills: dict[str, SkillAsset], name: str
) -> None:
    skill = skills[name]
    problems: list[str] = []
    for path in [*_skill_files(skill), *_example_files(skill)]:
        text = path.read_text(encoding="utf-8")
        commands = (
            quoted_commands.commands(text)
            if path.suffix == ".md"
            else quoted_commands.comment_commands(text)
        )
        problems += [f"{path.relative_to(skill.source)}: {p}" for p in _problems(root, commands)]

    assert problems == []


def test_the_starter_suite_comments_name_real_commands(root: App) -> None:
    text = starter_suite("Deploy", organization="Default", launch={}, survey=[])
    commands = list(quoted_commands.comment_commands(text))

    assert commands
    assert _problems(root, iter(commands)) == []


_REPO_ONLY = re.compile(
    r"(?<![\w.~/-])docs/|untaped repository|\bsrc/untaped\b|\bpackages/untaped|CONTRIBUTING\.md"
)


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


def _template_sections(path: Path) -> list[str]:
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    headings = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
    return [heading for heading in headings if heading in SKILL_SECTIONS]


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_follows_the_template_sections(skills: dict[str, SkillAsset], name: str) -> None:
    assert _template_sections(skills[name].source / "SKILL.md") == list(SKILL_SECTIONS)


def test_example_skill_follows_the_template_sections() -> None:
    assert _template_sections(EXAMPLE_SKILL) == [s for s in SKILL_SECTIONS if s != "References"]


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_files_stay_within_budget(skills: dict[str, SkillAsset], name: str) -> None:
    for path in _skill_files(skills[name]):
        budget = SKILL_BUDGET if path.name == "SKILL.md" else REFERENCE_BUDGET
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= budget, f"{path.name} has {lines} lines; split it by task"
