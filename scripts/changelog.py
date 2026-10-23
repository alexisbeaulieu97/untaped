"""Changelog fragments: one file per user-visible change, gathered at release.

Usage: ``uv run python scripts/changelog.py [--root DIR] <command>``.

- ``check`` validates every fragment in ``changelog.d/`` (file name, type,
  format, line width, link line). It runs as a pre-commit hook.
- ``draft [--pr N]`` prints the next release's section with each entry's PR
  link derived. A fragment not on ``origin/main`` yet gets ``--pr N`` (CI
  passes the event's) or, without it, a visible placeholder link.
- ``build VERSION`` writes ``## VERSION`` at the top of ``CHANGELOG.md`` and
  deletes the fragments. On a major release it moves the previous major's
  sections to ``changelog/<X>.x.md``. A later build of the same base version
  (``X.Y.Zrc2``, ``X.Y.Z``) retitles the pre-release section and adds any new
  fragments. Under Deprecated it writes one generated line for each
  deprecation whose removal version rose since the newest release tag
  (``tests/repo/surface.txt`` against that tag's copy), unless an entry
  already names the thing. It needs that tag: fetch tags first.

A fragment is ``changelog.d/<slug>.<type>.md`` holding one entry: the
sentence as it should read, wrapped at 78 columns (a line holding one longer
token, such as a URL, may exceed it), with no leading ``- ``, no heading and
no blank line. The renderer adds the bullet, indents the
continuation lines and appends the link ``([#N](…/pull/N))`` where N is the PR
whose merge commit on ``origin/main`` added the file. An entry that ends with
its own link group (issue links, several PRs) keeps it and gets none.

Every failure prints one line per error on stderr and exits 1.
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from release import baseline_tag  # scripts/release.py

from untaped.sdk.git import GitCommandError, run_git

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/alexisbeaulieu97/untaped"
FRAGMENT_DIR = "changelog.d"
#: Fragment types in the order their sections render.
TYPES = ("upgrading", "added", "changed", "deprecated", "removed", "fixed")
WIDTH = 80
#: A fragment line's limit: the rendered bullet or indent adds two columns.
FRAGMENT_WIDTH = WIDTH - 2
PLACEHOLDER = "PR"

_NAME = re.compile(r"^(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.(?P<type>[a-z]+)\.md$")
_LINK = rf"\[#\d+\]\({re.escape(REPO_URL)}/(?:pull|issues)/\d+\)"
_OWN_LINKS = re.compile(rf"\(\s*{_LINK}(?:,\s*{_LINK})*\s*\)\Z")
_PR_MERGE = re.compile(r"^Merge pull request #(\d+) from ")
_VERSION = re.compile(
    r"^(?P<base>(?P<major>\d+)\.(?P<minor>\d+)\.(?P<micro>\d+))(?:(?:a|b|rc)\d+)?$"
)
_PRE_RELEASE = re.compile(r"^(\d+\.\d+\.\d+)(?:a|b|rc)\d+$")
_SECTION_TITLE = re.compile(r"^(\d+)\.\d+\.\d+")
_ARCHIVE_LINK = re.compile(r"\[(\d+)\.x\]\(changelog/\1\.x\.md\)")
_NOT_ON_MAIN = 10**9
SURFACE_FILE = "tests/repo/surface.txt"
NO_TAG = "no release tag reachable: run `git fetch --tags origin` before `changelog.py build`"
_SDK_PLUGIN_MODULE = re.compile(r"^untaped_\w+\.(?:api|testing)\.")
_CLI_WORD = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ChangelogError(Exception):
    """A problem with the fragments or the changelog, one message per line."""

    def __init__(self, *messages: str) -> None:
        super().__init__("\n".join(messages))
        self.messages = messages


@dataclass(frozen=True)
class Fragment:
    """One validated fragment."""

    path: str  # relative to the repository root, e.g. ``changelog.d/x.added.md``
    slug: str
    type: str
    text: str  # the entry: no leading bullet, no trailing newline


@dataclass(frozen=True)
class Added:
    """The commit on main that added a fragment's file."""

    sha: str
    subject: str
    rank: int  # 0 is the oldest commit


def fragment_type(path: str) -> str | None:
    """The type of a ``changelog.d/<slug>.<type>.md`` path, or ``None`` if it isn't one."""
    directory, _, name = path.rpartition("/")
    match = _NAME.match(name)
    if directory != FRAGMENT_DIR or match is None or match["type"] not in TYPES:
        return None
    return match["type"]


def is_breaking(text: str) -> bool:
    """Whether an entry opens with the ``**Breaking (scope):**`` marker."""
    return text.lstrip().startswith("**Breaking")


def has_own_links(text: str) -> bool:
    """Whether the entry ends with a parenthesised group of this repo's PR or issue links."""
    return _OWN_LINKS.search(text.rstrip()) is not None


def parse_fragment(name: str, content: str) -> tuple[Fragment | None, list[str]]:
    """The fragment in ``changelog.d/<name>`` holding ``content``, or why it isn't one."""
    path = f"{FRAGMENT_DIR}/{name}"
    match = _NAME.match(name)
    if match is None:
        return None, [f"{path}: name it <slug>.<type>.md, with a kebab-case slug"]
    if match["type"] not in TYPES:
        return None, [f"{path}: type '{match['type']}' is not one of {', '.join(TYPES)}"]
    text = content.strip("\n")
    lines = text.split("\n")
    errors: list[str] = []
    if not text.strip():
        errors.append("is empty")
    else:
        if lines[0].startswith(("- ", "* ")):
            errors.append("starts with a bullet; the renderer adds it")
        if lines[0].startswith("#"):
            errors.append("starts with a heading; the section is the file's type")
        if any(not line.strip() for line in lines):
            errors.append("has a blank line; an entry is one paragraph")
        if lines[0] != lines[0].lstrip():
            errors.append("starts with whitespace")
        errors += [
            f"line {number} is {len(line)} columns; wrap at {FRAGMENT_WIDTH}"
            for number, line in enumerate(lines, 1)
            if len(line) > FRAGMENT_WIDTH and len(line.split()) > 1  # one long token can't wrap
        ]
        tail_start = text.rfind("([#")
        malformed = (
            tail_start != -1
            and text.endswith(")")
            and REPO_URL in text[tail_start:]
            and not _OWN_LINKS.match(text[tail_start:])
        )
        if malformed:
            errors.append(
                "ends with a malformed link group; write ([#N](URL), …) with this "
                "repository's pull or issues URLs, or drop it to get the derived link"
            )
    if errors:
        return None, [f"{path}: {error}" for error in errors]
    return Fragment(path, match["slug"], match["type"], text), []


def load_fragments(root: Path) -> list[Fragment]:
    """Every fragment under ``root`` (none without a ``changelog.d/``), sorted by path."""
    directory = root / FRAGMENT_DIR
    if not directory.is_dir():
        return []
    fragments: list[Fragment] = []
    errors: list[str] = []
    for file in sorted(directory.iterdir()):
        if not file.is_file():
            errors.append(f"{FRAGMENT_DIR}/{file.name}: only fragment files belong here")
            continue
        fragment, found = parse_fragment(file.name, file.read_text(encoding="utf-8"))
        errors += found
        if fragment is not None:
            fragments.append(fragment)
    if errors:
        raise ChangelogError(*errors)
    return fragments


def fragment_paths(root: Path) -> list[str]:
    """Paths (relative to ``root``) of every file in ``changelog.d/``."""
    directory = root / FRAGMENT_DIR
    if not directory.is_dir():
        return []
    return sorted(f"{FRAGMENT_DIR}/{file.name}" for file in directory.iterdir())


def _git(root: Path, *args: str) -> str:
    return run_git(list(args), timeout=60, cwd=root, capture=True).text


def main_history(root: Path) -> dict[str, Added]:
    """For each fragment on ``origin/main``, the latest first-parent commit there that added it.

    Empty when there is no ``origin/main``: nothing is on main then.
    """
    try:
        out = _git(
            root,
            "log",
            "--first-parent",
            "--diff-merges=first-parent",
            "--no-renames",
            "--diff-filter=A",
            "--name-only",
            "--format=%x00%H %s",
            "origin/main",
            "--",
            FRAGMENT_DIR,
        )
    except GitCommandError:
        return {}
    try:
        on_main = set(
            _git(root, "ls-tree", "-r", "--name-only", "origin/main", "--", FRAGMENT_DIR).split(
                "\n"
            )
        )
    except GitCommandError:
        return {}
    chunks = [chunk for chunk in out.split("\0") if chunk.strip()]
    found: dict[str, Added] = {}
    for position, chunk in enumerate(chunks):  # newest first
        header, *paths = chunk.splitlines()
        sha, _, subject = header.partition(" ")
        added = Added(sha, subject, rank=len(chunks) - position)
        for path in filter(None, (p.strip() for p in paths)):
            if path in on_main:  # a slug a release deleted is not on main until re-added there
                found.setdefault(path, added)
    return found


def _link(number: int | str) -> str:
    return f"([#{number}]({REPO_URL}/pull/{number}))"


def derive_link(fragment: Fragment, history: Mapping[str, Added], pr: int | None) -> str | None:
    """The PR link for a fragment, or ``None`` when it isn't on main and has no ``pr``."""
    added = history.get(fragment.path)
    if added is None:
        return None if pr is None else _link(pr)
    merge = _PR_MERGE.match(added.subject)
    if merge is None:
        raise ChangelogError(
            f"{fragment.path}: added by {added.sha[:9]} ({added.subject!r}), which is not a "
            "'Merge pull request' commit, so its PR is unknown; end the fragment with an "
            "explicit link group such as ([#N](" + REPO_URL + "/pull/N))"
        )
    return _link(int(merge[1]))


def render_entry(fragment: Fragment, link: str | None) -> str:
    """The fragment as a bullet; ``link`` joins the last line when it fits, else gets its own."""
    lines = fragment.text.split("\n")
    if link is not None:
        if 2 + len(lines[-1]) + 1 + len(link) <= WIDTH:
            lines[-1] += " " + link
        else:
            lines.append(link)
    return "\n".join(["- " + lines[0], *("  " + line for line in lines[1:])])


def collect(root: Path, *, pr: int | None = None, strict: bool = False) -> dict[str, list[str]]:
    """Rendered entries by type, in merge order. ``strict`` refuses a fragment not on main."""
    fragments = load_fragments(root)
    history = main_history(root)
    fragments.sort(
        key=lambda f: (history[f.path].rank if f.path in history else _NOT_ON_MAIN, f.slug)
    )
    entries: dict[str, list[str]] = {}
    for fragment in fragments:
        link = None
        if not has_own_links(fragment.text):
            if strict and fragment.path not in history:
                raise ChangelogError(
                    f"{fragment.path}: not on origin/main, so its PR is unknown; "
                    "merge the PR first, or end the fragment with an explicit link group"
                )
            link = derive_link(fragment, history, pr) or _link(PLACEHOLDER)
        entries.setdefault(fragment.type, []).append(render_entry(fragment, link))
    return entries


def render_body(preamble: str, entries: Mapping[str, Sequence[str]]) -> str:
    """The opening paragraph, then a ``###`` list per type."""
    blocks = [preamble] if preamble else []
    for kind in TYPES:
        if entries.get(kind):
            blocks += [f"### {kind.capitalize()}", "\n".join(entries[kind])]
    return "\n\n".join(blocks)


def draft_text(entries: Mapping[str, Sequence[str]]) -> str:
    """The next release's section, or ``""`` without fragments."""
    return "## Unreleased\n\n" + render_body("", entries) + "\n" if any(entries.values()) else ""


def _parse_body(body: str) -> tuple[str, dict[str, list[str]]]:
    """Split a built section's body into its opening paragraph and entries by type."""
    pieces = re.split(r"(?m)^### (.+)$", body)
    entries: dict[str, list[str]] = {}
    for heading, block in zip(pieces[1::2], pieces[2::2], strict=True):
        kind = heading.strip().lower()
        if kind not in TYPES:
            raise ChangelogError(f"CHANGELOG.md: cannot merge into the unknown '### {heading}'")
        entries[kind] = [e.strip("\n") for e in re.split(r"(?m)^(?=- )", block.strip()) if e]
    return pieces[0].strip(), entries


def _split_sections(changelog: str) -> tuple[str, list[tuple[str, str]]]:
    head, *pieces = re.split(r"(?m)^(?=## )", changelog)
    sections = []
    for piece in pieces:
        title, _, body = piece.partition("\n")
        sections.append((title.removeprefix("## ").strip(), body.strip("\n")))
    return head, sections


def _join(head: str, sections: Sequence[tuple[str, str]]) -> str:
    parts = [
        f"## {title}\n\n{body}".rstrip("\n") if body else f"## {title}" for title, body in sections
    ]
    return head.rstrip("\n") + "\n\n" + "\n\n".join(parts) + "\n"


def older_releases(majors: Sequence[int]) -> str:
    """The ``## Older releases`` body linking each archived major, newest first."""
    links = [f"[{m}.x](changelog/{m}.x.md)" for m in majors]
    names = ", ".join(links[:-1]) + f" and {links[-1]}" if len(links) > 1 else links[0]
    text = f"Each earlier major has its own file: {names}."
    return textwrap.fill(text, WIDTH, break_long_words=False, break_on_hyphens=False)


@dataclass(frozen=True)
class Fact:
    """A generated Deprecated entry: a removal version that rose since the last release."""

    name: str
    """The thing's short name, which an entry names to replace this line."""
    text: str


def short_name(identity: str) -> str:
    """What a surface line is called in prose, one rule per area.

    ``sdk`` lines drop the module (``untaped.sdk.report_error(exc)`` is
    ``report_error``); ``cli`` lines drop ``untaped`` and keep the path with
    its flag (``awx test run --dry-run``; an alias is its old spelling);
    ``cfg`` and ``state`` lines are the key (a rename's old key); a
    ``behaviour`` line is its name without the owner.
    """
    from untaped.core.surface import SDK_MODULES  # noqa: PLC0415

    area, _, rest = identity.partition(" ")
    rest = rest.partition(" -> ")[0]
    if area == "cli":
        words = rest.split(" ")[1:]
        kept: list[str] = []
        for word in words:
            if word.startswith("-"):
                kept.append(word)
                break
            if not _CLI_WORD.match(word):
                break
            kept.append(word)
        return " ".join(kept)
    if area == "sdk":
        subject = re.split(r"[( ]", rest, maxsplit=1)[0]
        plugin = _SDK_PLUGIN_MODULE.match(subject)
        if plugin is not None:
            return subject[plugin.end() :]
        modules = sorted(SDK_MODULES, key=len, reverse=True)
        module = next((m for m in modules if subject.startswith(f"{m}.")), None)
        return subject if module is None else subject[len(module) + 1 :]
    if area == "behaviour":
        return rest.partition(".")[2] or rest
    return rest.split(" ")[0]


def names(entry: str, name: str) -> bool:
    """Whether ``entry`` names ``name`` as a whole token.

    ``report_error`` is not ``report_errors``, and ``awx test run --dry-run``
    is not ``auth migrate --dry-run``; ``untaped.sdk.report_error`` names it.
    """
    pattern = rf"(?<![\w-]){re.escape(name)}(?![\w-]|\.\w)"
    return re.search(pattern, " ".join(entry.split())) is not None


def postponement_facts(baseline: str | None, current: str | None) -> list[Fact]:
    """One fact per thing whose removal version rose between two surface files' texts.

    A mark reaches every line under the thing (a command's flags and choices,
    a callable's parameters, a class's members), so its lines share one fact:
    a line whose short name extends another moved line's by a word or a
    member (``plugin check --format`` under ``plugin check``,
    ``AppContext.diag`` under ``AppContext``), with the same dates, adds none.
    """
    from untaped.core.surface import parse, postponements  # noqa: PLC0415

    if baseline is None or current is None:
        return []
    raised: dict[str, tuple[int, int]] = {}
    for moved in postponements(parse(baseline), parse(current)):
        if moved.now > moved.was:  # a date that fell: test_major_release.py refuses it
            raised.setdefault(short_name(moved.line.identity), (moved.was, moved.now))
    return [
        Fact(name, f"`{name}` will now be removed in {now}.0 instead of {was}.0.")
        for name, (was, now) in raised.items()
        if not any(
            name.startswith((f"{other} ", f"{other}.")) and raised[other] == (was, now)
            for other in raised
            if other != name
        )
    ]


def missing_facts(entries: Sequence[str], facts: Sequence[Fact]) -> list[Fact]:
    """The facts no entry of ``entries`` names."""
    return [fact for fact in facts if not any(names(entry, fact.name) for entry in entries)]


def surface_facts(root: Path) -> list[Fact]:
    """The facts for ``root``: its surface file against the newest reachable release tag's.

    Raises :class:`ChangelogError` with :data:`NO_TAG` when no tag is reachable.
    """
    tag = baseline_tag(
        _git(root, "tag", "--list", "v*", "--merged", "HEAD").split(), prereleases=True
    )
    if tag is None:
        raise ChangelogError(NO_TAG)
    current = root / SURFACE_FILE
    try:
        baseline = _git(root, "show", f"{tag}:{SURFACE_FILE}")
    except GitCommandError:
        baseline = None  # the tag predates the file
    text = current.read_text(encoding="utf-8") if current.is_file() else None
    return postponement_facts(baseline, text)


def _fact_entry(fact: Fact) -> str:
    lines = textwrap.wrap(fact.text, FRAGMENT_WIDTH, break_long_words=False, break_on_hyphens=False)
    return "\n".join(["- " + lines[0], *("  " + line for line in lines[1:])])


def build_text(
    changelog: str,
    version: str,
    entries: Mapping[str, Sequence[str]],
    facts: Sequence[Fact] = (),
) -> tuple[str, dict[int, str]]:
    """The changelog after releasing ``version``, and the archive files to write by major.

    Each of ``facts`` that no Deprecated entry of this build's fragments names
    joins the Deprecated list. A pre-release section's entries never silence
    one: the facts compare with the newest tag, that pre-release included, so
    a fact here is a change since its section was written.
    """
    parsed = _VERSION.match(version)
    if parsed is None:
        raise ChangelogError(f"'{version}' is not X.Y.Z or a pre-release X.Y.Z(a|b|rc)N")
    is_major = parsed["minor"] == "0" and parsed["micro"] == "0"
    if entries.get("upgrading") and not is_major:
        raise ChangelogError(
            f"{version} is not a major release but has upgrading fragments (a breaking "
            "change merged before its major); hold them back or release the major"
        )
    head, sections = _split_sections(changelog)
    if any(title == version for title, _ in sections):
        raise ChangelogError(f'CHANGELOG.md already has a "## {version}" section')
    previous = None
    if sections:
        match = _PRE_RELEASE.match(sections[0][0])
        if match and match[1] == parsed["base"]:
            previous = sections.pop(0)
    if previous is None and not any(entries.values()) and not facts:
        raise ChangelogError("nothing to release: changelog.d/ holds no fragments")
    preamble, merged = _parse_body(previous[1]) if previous else ("", {})
    combined = {k: [*merged.get(k, []), *entries.get(k, [])] for k in TYPES}
    combined["deprecated"] += [
        _fact_entry(fact) for fact in missing_facts(entries.get("deprecated", []), facts)
    ]
    archives: dict[int, str] = {}
    if is_major:
        keep: list[tuple[str, str]] = []
        moved: dict[int, list[tuple[str, str]]] = {}
        for title, body in sections:
            old = _SECTION_TITLE.match(title)
            if old and int(old[1]) != int(parsed["major"]):
                moved.setdefault(int(old[1]), []).append((title, body))
            else:
                keep.append((title, body))
        sections = keep
        for major, items in moved.items():
            intro = (
                f"# Changelog: untaped {major}.x\n\n"
                "Newer releases are in [CHANGELOG.md](../CHANGELOG.md).\n"
            )
            archives[major] = _join(intro, items)
        if archives:
            sections = _with_archives(sections, sorted(archives, reverse=True))
    new = (version, render_body(preamble, combined))
    return _join(head, [new, *sections]), archives


def _with_archives(sections: list[tuple[str, str]], added: Sequence[int]) -> list[tuple[str, str]]:
    """``sections`` with ``## Older releases`` linking the ``added`` majors too."""
    out = [(t, b) for t, b in sections if t != "Older releases"]
    old = [b for t, b in sections if t == "Older releases"]
    known = {int(m) for m in _ARCHIVE_LINK.findall(old[0])} if old else set()
    out.append(("Older releases", older_releases(sorted(known | set(added), reverse=True))))
    return out


def build(root: Path, version: str) -> None:
    """Release ``version``: rewrite CHANGELOG.md, write archives, delete the fragments."""
    facts = surface_facts(root)
    entries = collect(root, strict=True)
    changelog_path = root / "CHANGELOG.md"
    text, archives = build_text(changelog_path.read_text(encoding="utf-8"), version, entries, facts)
    for major in archives:
        if (root / "changelog" / f"{major}.x.md").exists():
            raise ChangelogError(f"changelog/{major}.x.md already exists")
    for major, archive in archives.items():
        (root / "changelog").mkdir(exist_ok=True)
        (root / "changelog" / f"{major}.x.md").write_text(archive, encoding="utf-8")
    changelog_path.write_text(text, encoding="utf-8")
    for path in fragment_paths(root):
        (root / path).unlink()
    directory = root / FRAGMENT_DIR
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="changelog.py", description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate every fragment")
    draft = commands.add_parser("draft", help="print the next release's section")
    draft.add_argument("--pr", type=int, help="the PR number for fragments not on main yet")
    commands.add_parser("build", help="release VERSION").add_argument("version")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            load_fragments(args.root)
        elif args.command == "draft":
            sys.stdout.write(draft_text(collect(args.root, pr=args.pr)))
        else:
            build(args.root, args.version)
    except ChangelogError as exc:
        for message in exc.messages:
            print(message, file=sys.stderr)
        return 1
    except GitCommandError as exc:
        print(f"changelog: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
