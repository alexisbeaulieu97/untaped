"""Documentation checks: generated config reference, links and command examples.

- ``docs/reference/config.md`` must match ``scripts/gen_config_reference.py``
  output, and every setting must have a description.
- Every relative Markdown link (and ``#anchor``) in ``docs/``, ``README.md``,
  ``AGENTS.md``, ``CONTRIBUTING.md`` and the packaged skills must resolve, so
  renaming a heading cannot silently break a pointer to it.
- Every ``untaped`` example in a ``bash`` block must name a real command and
  only options that command accepts.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from pathlib import Path

import gen_config_reference as generator
import pytest
from cyclopts import App

from repo.support import REPO_ROOT
from untaped.bootstrap import build_root_app
from untaped.capabilities import registry
from untaped.capabilities.registry import CapabilitySpec, ProviderCandidate

REGENERATE = "uv run python scripts/gen_config_reference.py"

_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
_FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.MULTILINE)


def test_config_reference_is_current() -> None:
    page = (REPO_ROOT / "docs" / "reference" / "config.md").read_text(encoding="utf-8")
    assert page == generator.render(), f"docs/reference/config.md is stale; run: {REGENERATE}"


def test_every_setting_has_a_description() -> None:
    assert generator.missing_descriptions() == [], (
        "add Field(description=...) or a DESCRIPTIONS entry in scripts/gen_config_reference.py"
    )
    assert generator.unknown_descriptions() == [], "DESCRIPTIONS names a setting that is gone"


def test_config_reference_refuses_a_quarantined_first_party_capability(
    monkeypatch: pytest.MonkeyPatch,
    broken_first_party_candidates: Callable[[], tuple[ProviderCandidate, ...]],
) -> None:
    monkeypatch.setattr(registry, "discover_candidates", broken_first_party_candidates)
    with pytest.raises(RuntimeError) as failed:
        generator.collect_sections()
    message = str(failed.value)
    assert message.startswith("first-party capabilities quarantined; fix them before generating")
    for name in ("awx", "jira"):
        assert f"{name!r} [malformed-entry-point]: could not resolve entry point" in message


def _markdown_files() -> list[Path]:
    files = sorted((REPO_ROOT / "docs").rglob("*.md"))
    skills = sorted(REPO_ROOT.glob("packages/*/src/**/skills/**/*.md"))
    root = (REPO_ROOT / name for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md"))
    return [*files, *skills, *root]


def _slug(heading: str) -> str:
    # GitHub keeps underscores and hyphens and drops other punctuation.
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    return {_slug(match) for match in _HEADING.findall(text)}


def _broken_links(path: Path) -> list[str]:
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    text = re.sub(r"(`+)[^\n]*?\1", "", text)
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


def _unknown_options(root: App, argv: list[str], aliases: set[str]) -> list[str]:
    app, path, flags, words = root, [], [], []
    skip_value = False
    for token in argv:
        if token == "--":
            break  # the rest is data (``alias set NAME -- COMMAND…``)
        if skip_value:
            skip_value = False
        elif token.startswith("-") and token != "-":  # a bare ``-`` names stdin
            flags.append(token.split("=")[0])
            skip_value = token == "--profile"
        elif token in app:
            app = app[token]
            path.append(token)
        else:
            words.append(token)
    if not path:
        if words[:1] == [_EXAMPLE_PROVIDER] or (words and words[0] in aliases):
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


def test_command_examples_use_real_commands_and_options(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    """Every ``untaped`` example in a ``bash`` block names a real command and options."""
    root = build_root_app(candidates=first_party_candidates)
    problems = []
    for path in _markdown_files():
        for block in _bash_blocks(path):
            commands = _untaped_commands(block)
            # A block may run an alias it defines (``alias set NAME -- …``).
            aliases = {argv[2] for argv in commands if argv[:2] == ["alias", "set"] and argv[2:]}
            for argv in commands:
                problems.extend(
                    f"{path.relative_to(REPO_ROOT)}: {problem}"
                    for problem in _unknown_options(root, argv, aliases)
                )
    assert problems == []


DOCS_PAGES = [
    "configuration.md",
    "getting-started.md",
    "plugins.md",
    "reference/config.md",
    "scripting.md",
]  # Tasks 2-5 shrink docs/ to exactly this


def test_scripting_keeps_an_anchor_per_capability(
    first_party_specs: tuple[CapabilitySpec, ...],
) -> None:
    anchors = _anchors(REPO_ROOT / "docs" / "scripting.md")
    for name in (
        "exit-codes",
        "categories",
        "precedence",
        "stderr-diagnostics",
        "environment-variables",
        "output-records",
    ):
        assert name in anchors
    assert {spec.name for spec in first_party_specs} <= anchors


def test_install_examples_use_the_extras() -> None:
    for page in (REPO_ROOT / "README.md", REPO_ROOT / "docs" / "getting-started.md"):
        installs = [
            line.strip()
            for block in _FENCE.finditer(page.read_text(encoding="utf-8"))
            for line in block.group().splitlines()
            if re.match(r"\s*(uv tool install|pip install)\b", line)
        ]
        assert installs, page
        assert any("untaped[all]" in line for line in installs), page
        bare = [line for line in installs if re.search(r"install\b.*?['\"]?untaped-[a-z]", line)]
        assert not bare, page


def test_docs_holds_only_the_reader_pages() -> None:
    docs = REPO_ROOT / "docs"
    pages = sorted(str(p.relative_to(docs)) for p in docs.rglob("*.md"))
    assert set(pages) >= set(DOCS_PAGES)


@pytest.mark.parametrize(
    ("heading", "slug"),
    [("`failed_tasks`", "failed_tasks"), ("Header: `variables`", "header-variables")],
)
def test_heading_slugs_match_github(heading: str, slug: str) -> None:
    assert _slug(heading) == slug


def test_links_inside_double_backtick_code_are_ignored(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text("Literal ``[example](missing.md)`` text.\n", encoding="utf-8")

    assert _broken_links(page) == []
