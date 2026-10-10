"""Documentation checks: generated config reference, links and command examples.

- ``docs/reference/config.md`` must match ``scripts/gen_config_reference.py``
  output, and every setting must have a description.
- Every relative Markdown link (and ``#anchor``) in ``docs/``, ``README.md``,
  ``AGENTS.md``, ``CONTRIBUTING.md`` and the packaged skills must resolve, so
  renaming a heading cannot silently break a pointer to it.
- Every ``untaped`` example in a ``bash`` block must name a real command and
  only options that command accepts; every ``untaped …`` in inline code, and
  every command a setting description names, must name a real command.
- Docs pages stay within :data:`DOCS_PAGE_BUDGET` lines, and Markdown avoids
  the retired terms in :data:`RETIRED_TERMS`.
"""

from __future__ import annotations

import ast
import functools
import itertools
import re
import shlex
import tomllib
from collections.abc import Callable
from pathlib import Path

import gen_config_reference as generator
import pytest
import release
from cyclopts import App

from repo import quoted_commands
from repo.support import FENCE, PACKAGES, REPO_ROOT, markdown_files
from untaped.bootstrap import build_root_app
from untaped.plugins import registry
from untaped.plugins.registry import PluginCandidate, PluginSpec

REGENERATE = "uv run python scripts/gen_config_reference.py"

_LINK_BODY = r"\[[^\]]*\]\(([^)\s]+)\)"
_LINK = re.compile(r"(?<!!)" + _LINK_BODY)
_BARE_INSTALL = re.compile(r"\binstall\b.*?['\"]?untaped-[a-z]")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.MULTILINE)


def test_config_reference_is_current() -> None:
    page = (REPO_ROOT / "docs" / "reference" / "config.md").read_text(encoding="utf-8")
    assert page == generator.render(), f"docs/reference/config.md is stale; run: {REGENERATE}"


def test_every_setting_has_a_description() -> None:
    assert generator.missing_descriptions() == [], (
        "add Field(description=...) or a DESCRIPTIONS entry in scripts/gen_config_reference.py"
    )
    assert generator.unknown_descriptions() == [], "DESCRIPTIONS names a setting that is gone"


_SETTING_ROW = re.compile(r"^\| `([a-z_]+)\.[^`]+` \|.*\| (.*) \|$", re.MULTILINE)
_COMMAND_SPAN = re.compile(r"`([a-z][a-z-]*(?: [a-z][a-z-]*)+)((?: --[a-z-]+)*)`")


def _setting_command_problems(root: App, page: str) -> list[str]:
    """Commands a setting description names (``source refresh --parallel``) that do not exist.

    A span of two or more words is a command, named from the root or from the
    setting's own plugin; its options must exist too. One word is ambiguous
    with a value (``table``, ``never``) and is not checked.
    """
    problems = []
    for section, description in _SETTING_ROW.findall(page):
        for span, flags in _COMMAND_SPAN.findall(description):
            words = span.split()
            path = next(
                (
                    base + words
                    for base in ([], [section])
                    if not root.parse_commands(base + words)[2]
                ),
                None,
            )
            if path is None:
                problems.append(f"{section}: `{span}` is not a command")
            elif flags:
                problems += [
                    f"{section}: {p}" for p in _unknown_options(root, path + flags.split(), set())
                ]
    return problems


def test_setting_descriptions_name_real_commands(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    assert _setting_command_problems(root, generator.render()) == []


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        ("| `recipe.keep` | int | `1` | `X` | `backups prune` keeps this many. |", None),
        (
            "| `recipe.keep` | int | `1` | `X` | `backup prune` keeps this many. |",
            "recipe: `backup prune` is not a command",
        ),
        ("| `github.n` | int | `4` | `X` | Default `cache sync --parallel`. |", None),
        (
            "| `github.n` | int | `4` | `X` | Default `cache sync --jobs`. |",
            "github: github cache sync: --jobs",
        ),
        ("| `awx.n` | int | `4` | `X` | Read by `github sweep`. |", None),
    ],
)
def test_setting_command_detector(
    first_party_candidates: tuple[PluginCandidate, ...], row: str, problem: str | None
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    assert _setting_command_problems(root, row) == ([problem] if problem else [])


def test_config_reference_refuses_a_quarantined_first_party_plugin(
    monkeypatch: pytest.MonkeyPatch,
    broken_first_party_candidates: Callable[[], tuple[PluginCandidate, ...]],
) -> None:
    monkeypatch.setattr(registry, "discover_candidates", broken_first_party_candidates)
    with pytest.raises(RuntimeError) as failed:
        generator.collect_sections()
    message = str(failed.value)
    assert message.startswith("first-party plugins quarantined; fix them before generating")
    for name in ("awx", "jira"):
        assert f"{name!r} [malformed-entry-point]: could not resolve entry point" in message


def _package_readmes() -> list[Path]:
    return sorted(PACKAGES.glob("*/README.md"))


def _slug(heading: str) -> str:
    # GitHub keeps underscores and hyphens and drops other punctuation.
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


@functools.cache
def _anchors(path: Path) -> set[str]:
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    return {_slug(match) for match in _HEADING.findall(text)}


def _prose(path: Path) -> str:
    """The page text without fenced blocks or inline code."""
    return re.sub(r"(`+)[^\n]*?\1", "", FENCE.sub("", path.read_text(encoding="utf-8")))


def _broken_links(path: Path) -> list[str]:
    text = _prose(path)
    broken: list[str] = []
    # A line break inside a link target makes CommonMark render it as text.
    broken += re.findall(r"\]\(([^)\s]*\n[^)]*)\)", text)
    for target in _LINK.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):
            continue
        ref, _, anchor = target.partition("#")
        resolved = (path.parent / ref).resolve() if ref else path
        checks_anchor = bool(anchor) and resolved.suffix == ".md"
        if not resolved.exists() or (checks_anchor and anchor not in _anchors(resolved)):
            broken.append(target)
    return broken


#: The changelog, its per-major archives and the unreleased fragments: link-checked, but
#: history, so no other docs checks.
CHANGELOGS = [
    REPO_ROOT / "CHANGELOG.md",
    *sorted((REPO_ROOT / "changelog").glob("*.md")),
    *sorted((REPO_ROOT / "changelog.d").glob("*.md")),
]


@pytest.mark.parametrize(
    "path", [*markdown_files(), *CHANGELOGS], ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_relative_links_resolve(path: Path) -> None:
    assert _broken_links(path) == []


# release.MAIN_LINK (whose group 1 is the URL before ``/main``), then the path and anchor.
_REPO_URL = re.compile(release.MAIN_LINK.pattern + r"/([^)\s#]+)(?:#([^)\s]+))?")
# Any link to a file in this repository: ``<REPO_URL>/<kind>/<ref>`` (issue, PR and release
# links name no ref and need no pinning; trailing punctuation is not part of the ref).
_REPO_REF = re.compile(
    re.escape(release.REPO_URL) + r"/(blob|tree|raw|blame|edit)/([^/\s)#]+?)[.,;:]*(?=[/\s)#]|$)"
)


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_repository_urls_resolve(path: Path) -> None:
    """Absolute links into this repository (package READMEs render on PyPI) point at real files."""
    broken = []
    for _, target, anchor in _REPO_URL.findall(path.read_text(encoding="utf-8")):
        file = REPO_ROOT / target
        if not file.exists() or (anchor and file.suffix == ".md" and anchor not in _anchors(file)):
            broken.append(f"{target}#{anchor}" if anchor else target)
    assert broken == []


_LINK_TARGETS = (
    # Inline links and images, with an optional title: [x](target "title").
    re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[^)]*)?\)"),
    # Reference-style definitions: [x]: target "title".
    re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?", re.MULTILINE),
    # HTML anchors and images.
    re.compile(r"<(?:a|img)\b[^>]*?\b(?:href|src)\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE),
)


def _relative_targets(text: str) -> list[str]:
    """Link and image targets in Markdown prose that are not absolute URLs."""
    targets = [t for pattern in _LINK_TARGETS for t in pattern.findall(text)]
    return [t for t in targets if "://" not in t]


@pytest.mark.parametrize(
    "text",
    [
        "[x](docs/a.md)",
        "![x](logo.png)",
        "[x](#anchor)",
        '[x](docs/a.md "title")',
        "[x]: docs/a.md",
        '[x]: <docs/a.md> "title"',
        '<a href="docs/a.md">x</a>',
        "<img alt='x' src='logo.png'>",
    ],
)
def test_relative_targets_finds_every_link_form(text: str) -> None:
    assert _relative_targets(text) != []


def test_broken_links_reports_a_wrapped_target(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text("See [page.md](pa-\nge.md).\n", encoding="utf-8")
    assert _broken_links(page) == ["pa-\nge.md"]


def test_relative_targets_skips_absolute_urls() -> None:
    text = (
        '[x](https://e.com/a "t")\n[y]: https://e.com/b\n'
        '<a href="https://e.com/c">c</a> <img src="https://e.com/d.png">'
    )
    assert _relative_targets(text) == []


def test_package_readmes_link_absolutely() -> None:
    """PyPI cannot resolve a relative or in-page link or an image in a package README."""
    for readme in _package_readmes():
        assert _relative_targets(_prose(readme)) == [], readme


def test_package_readmes_link_into_the_repository_on_main() -> None:
    """The release pins ``blob/main`` and ``tree/main`` links to its tag (``release.py readmes``).

    Any other kind or ref (``raw``, ``HEAD``, a tag left by a local ``readmes``
    run) would reach PyPI unpinned or stale.
    """
    for readme in _package_readmes():
        refs = _REPO_REF.findall(readme.read_text(encoding="utf-8"))
        assert [ref for ref in refs if ref not in {("blob", "main"), ("tree", "main")}] == [], (
            readme
        )


def test_every_workspace_member_has_a_readme() -> None:
    members = {p.parent for p in PACKAGES.glob("*/pyproject.toml")}
    assert {r.parent for r in _package_readmes()} == members


_ROOT_OPTIONS = {"--profile", "--verbose", "-v", "--quiet", "-q", "--help", "-h"}
_ROOT_ONLY_OPTIONS = _ROOT_OPTIONS | {"--version", "--install-completion"}
_EXAMPLE_PROVIDER = "acme"

#: The plugins under ``examples/``: never installed here, so their commands don't resolve.
_EXAMPLE_PLUGINS = frozenset(
    name
    for pyproject in REPO_ROOT.glob("examples/*/pyproject.toml")
    for name in tomllib.loads(pyproject.read_text())["project"]["entry-points"]["untaped.plugins"]
)
"""The example external plugin in docs/plugins.md."""


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
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    """Every ``untaped`` example in a ``bash`` block names a real command and options."""
    root = build_root_app(candidates=first_party_candidates)
    problems = []
    for path in markdown_files():
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


def _prose_command_problem(root: App, command: str, aliases: set[str]) -> str | None:
    """Why the inline ``command`` names no real command (``None`` when it does).

    A placeholder in command position (``untaped awx <resource> edit``) ends
    the check: only the words before it must resolve.
    """
    words = list(itertools.takewhile(lambda t: not t.startswith("-"), shlex.split(command)[1:]))
    if words[:1] and words[0] in aliases | {_EXAMPLE_PROVIDER, *_EXAMPLE_PLUGINS}:
        return None
    for index, word in enumerate(words):
        if re.fullmatch(r"<[^>]+>", word) or quoted_commands.PLACEHOLDER.match(word):
            _, _, unused = root.parse_commands(words[:index])
            return f"not a command: {' '.join(unused)}" if unused else None
    return quoted_commands.parse_problem(root, command, inline=True)


def test_inline_commands_name_real_commands(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    """Every ``untaped …`` in inline code names a real command (skills are checked on their own)."""
    root = build_root_app(candidates=first_party_candidates)
    pages = [path for path in markdown_files() if "skills" not in path.parts]
    aliases = {
        argv[2]
        for path in pages
        for block in _bash_blocks(path)
        for argv in _untaped_commands(block)
        if argv[:2] == ["alias", "set"] and argv[2:]
    }
    problems = [
        f"{path.relative_to(REPO_ROOT)}: {command}: {problem}"
        for path in pages
        for command, inline in quoted_commands.commands(path.read_text(encoding="utf-8"))
        if inline and (problem := _prose_command_problem(root, command, aliases)) is not None
    ]
    assert problems == []


@pytest.mark.parametrize(
    ("command", "fails"),
    [
        ("untaped recipe backups prune", False),
        ("untaped recipe backup prune", True),
        ("untaped awx <resource> edit", False),
        ("untaped awxx <resource> edit", True),
        ("untaped COMMAND --help", False),
        ("untaped failed --limit 5", False),
        ("untaped github repos list --nope", True),
    ],
)
def test_inline_command_detector(
    first_party_candidates: tuple[PluginCandidate, ...], command: str, fails: bool
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    assert (_prose_command_problem(root, command, {"failed"}) is not None) is fails


DOCS_PAGES = [
    "composition.md",
    "configuration.md",
    "contracts.md",
    "getting-started.md",
    "plugin-skills.md",
    "plugins.md",
    "reference/config.md",
    "reference/conventions.md",
    "reference/environment.md",
    "reference/exit-codes.md",
    "reference/records.md",
    "screens.md",
    "scripting.md",
    "skills.md",
    "troubleshooting.md",
    "versioning.md",
]
#: Longest a docs page may grow, in lines. An over-budget page is split, not exempted.
DOCS_PAGE_BUDGET = 400
#: Retired term -> the term to use instead.
RETIRED_TERMS = {"root shell": "root", "unified shell": "root"}


def test_records_page_has_a_section_per_plugin(
    first_party_specs: tuple[PluginSpec, ...],
) -> None:
    records = _anchors(REPO_ROOT / "docs" / "reference" / "records.md")
    assert {spec.name for spec in first_party_specs} <= records


@pytest.mark.parametrize("page", DOCS_PAGES)
def test_docs_pages_stay_within_budget(page: str) -> None:
    lines = len((REPO_ROOT / "docs" / page).read_text(encoding="utf-8").splitlines())
    assert lines <= DOCS_PAGE_BUDGET, f"docs/{page} has {lines} lines; split it by reader task"


def test_docs_avoid_retired_terms() -> None:
    found = [
        f"{page.relative_to(REPO_ROOT)}: {term!r}, say {use!r}"
        for page in markdown_files()
        for term, use in RETIRED_TERMS.items()
        if term in page.read_text(encoding="utf-8").lower()
    ]
    assert found == []


def test_install_examples_use_the_extras() -> None:
    for page in (REPO_ROOT / "README.md", REPO_ROOT / "docs" / "getting-started.md"):
        installs = [
            line.strip()
            for block in FENCE.finditer(page.read_text(encoding="utf-8"))
            for line in block.group().splitlines()
            if re.match(r"\s*(uv tool install|pip install)\b", line)
        ]
        assert installs, page
        assert any("untaped[all]" in line for line in installs), page


def _bare_installs(path: Path, *, fenced_only: bool) -> list[str]:
    """Lines installing ``untaped-<name>`` alone; prose explaining the failure is allowed
    where ``fenced_only`` (the root README and getting-started)."""
    text = path.read_text(encoding="utf-8")
    if fenced_only:
        lines = [line for block in FENCE.finditer(text) for line in block.group().splitlines()]
    else:
        lines = text.splitlines()
    return [line.strip() for line in lines if _BARE_INSTALL.search(line)]


@pytest.mark.parametrize(
    ("path", "fenced_only"),
    [
        *((REPO_ROOT / "README.md", True), (REPO_ROOT / "docs" / "getting-started.md", True)),
        *((readme, False) for readme in _package_readmes()),
    ],
    ids=lambda v: str(v.relative_to(REPO_ROOT)) if isinstance(v, Path) else "",
)
def test_docs_never_install_a_bare_plugin_package(path: Path, *, fenced_only: bool) -> None:
    """Installing `untaped-<name>` alone leaves the core out; docs point at the extras."""
    assert _bare_installs(path, fenced_only=fenced_only) == []


def test_docs_holds_only_the_reader_pages() -> None:
    docs = REPO_ROOT / "docs"
    pages = sorted(str(p.relative_to(docs)) for p in docs.rglob("*.md"))
    assert pages == DOCS_PAGES


def test_agents_md_is_short_and_points_to_contributing() -> None:
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    # AGENTS.md carries the shared vision and agent-only notes; everything else
    # lives in CONTRIBUTING.md so the two never drift apart.
    assert len(agents.splitlines()) <= 40, "move detail to CONTRIBUTING.md"
    assert "CONTRIBUTING.md" in agents
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").splitlines()
    for heading in (
        "## Releasing",
        "## Evaluating a skill change",
        "## Adding a first-party plugin",
    ):
        assert heading in contributing


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


def _docstring(path: Path) -> str:
    return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""


def test_rationale_lives_beside_the_code_it_protects() -> None:
    assert not (REPO_ROOT / ".planning" / "decisions").exists()
    awx = PACKAGES / "untaped-awx/src/untaped_awx"
    assert "round-trip" in _docstring(awx / "infrastructure/yaml_io.py")
    workspace = PACKAGES / "untaped-workspace/src/untaped_workspace"
    assert "load-bearing" in _docstring(workspace / "infrastructure/git_worktrees.py")
    provision = _docstring(workspace / "application/provision.py")
    # the docstring names the workspace lock before the cache lock
    assert provision.index("workspace lock") < provision.index("cache lock")
