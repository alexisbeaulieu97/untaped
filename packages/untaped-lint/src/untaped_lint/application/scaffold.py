"""``untaped lint new``: a repository rule and its test, written fixtures first.

The rule module goes in the key's rules directory (``.untaped/lint/`` unless
the lint file says otherwise) and reads the repository's files
(``reads=Tree``): the model, never a kind string, because the next agent
copies the example. With a pytest configuration at the root the test goes in
``tests/lint/``, through ``untaped_lint.testing``; without one, a good and a
bad fixture directory beside the rules, and the two ``untaped lint``
commands that check them. The test fails until the rule is written. The lint
file and its ``linters:`` entry are created when missing.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from untaped.sdk import ErrorCategory, UntapedError, UsageError, checkout_root
from untaped_lint.application import repo_file
from untaped_lint.application.hints import lint_line
from untaped_lint.records import RULE_ID, ScaffoldOutcome

_ID = re.compile(RULE_ID)


@dataclass
class Scaffolded:
    """What ``lint new`` wrote, and what to run next."""

    files: list[ScaffoldOutcome] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


def scaffold(
    start: Path,
    rule_id: str,
    *,
    key: str | None = None,
    installed: list[str] | None = None,
    dry_run: bool = False,
) -> Scaffolded:
    """Write (or, with ``dry_run``, plan) rule ``rule_id`` for the repository at ``start``."""
    if not _ID.match(rule_id):
        raise UsageError(f"{rule_id!r} is not a rule id: lowercase words joined by hyphens")
    found = repo_file.find(start)
    root = (
        repo_file.root_of(found) if found is not None else checkout_root(start) or start.resolve()
    )
    lint_file = found or repo_file.lint_file(root)
    current = repo_file.read(found, installed=installed or ()) if found is not None else None
    key = key or (current.name if current is not None else None)
    if key is None:
        raise UsageError(
            "there is no .untaped/lint.yml yet: name its key with --key, as --key house"
        )
    repo_file.check_key(key, lint_file, set(installed or ()))
    rules_dir = (
        current.rules[key]
        if current is not None and key in current.rules
        else repo_file.rules_dir(root)
    )
    module = rules_dir / f"{_python_name(rule_id)}.py"
    files: dict[Path, str] = {module: _module(rule_id, key)}
    done = Scaffolded()
    public = f"{key}.{rule_id}"
    if _has_pytest(root):
        files[root / "tests" / "lint" / f"test_{_python_name(rule_id)}.py"] = _test(rule_id, key)
        done.next_steps.append(
            f"write the bad and good examples in the test, then the rule, until "
            f"`uv run pytest tests/lint` passes and `{lint_line('--select', public)}` exits 0"
        )
    else:
        fixtures = rules_dir / repo_file.FIXTURES / rule_id
        for kind, text in (("bad", _BAD), ("good", _GOOD)):
            files[fixtures / kind / "example.txt"] = text
            config = os.path.relpath(lint_file, fixtures / kind)
            exit_code = "3" if kind == "bad" else "0"
            done.next_steps.append(
                f"the {kind} example exits {exit_code}: "
                f"(cd {(fixtures / kind).relative_to(root).as_posix()} && "
                f"{lint_line('--config', config, '--select', public)})"
            )
    taken = [path for path in files if path.exists()]
    if taken:
        raise UntapedError(
            f"{taken[0]} already exists; pick another rule id",
            category=ErrorCategory.CONFLICT,
            system="local",
        )
    entry = _linters_entry(lint_file, current, key, rules_dir, root)
    action = "planned" if dry_run else "created"
    for path, text in files.items():
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        done.files.append(ScaffoldOutcome(action=action, target_path=path))
    if entry is not None:
        text, created = entry
        if not dry_run:
            lint_file.parent.mkdir(parents=True, exist_ok=True)
            lint_file.write_text(text, encoding="utf-8")
        done.files.append(
            ScaffoldOutcome(
                action="planned" if dry_run else ("created" if created else "updated"),
                target_path=lint_file,
            )
        )
    return done


def _python_name(rule_id: str) -> str:
    return rule_id.replace("-", "_")


def _has_pytest(root: Path) -> bool:
    """A pytest configuration at ``root``: ``[tool.pytest…]``, ``pytest.ini`` or ``conftest.py``."""
    if (root / "pytest.ini").is_file() or (root / "conftest.py").is_file():
        return True
    project = root / "pyproject.toml"
    try:
        data = tomllib.loads(project.read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
        return False
    return "pytest" in data.get("tool", {})


def _linters_entry(
    lint_file: Path,
    current: repo_file.RepoFile | None,
    key: str,
    rules_dir: Path,
    root: Path,
) -> tuple[str, bool] | None:
    """The lint file's new text and whether it is new, or ``None`` when nothing changes.

    The entry goes in as text, so the file keeps its comments and order; a
    result that doesn't parse back to the same linters plus this one (a flow
    mapping, an unusual layout) is refused rather than written.
    """
    relative = rules_dir.relative_to(root).as_posix()
    if current is None:
        return f"version: 1\nname: {key}\nlinters:\n  {key}:\n    rules: {relative}\n", True
    if key in current.rules:
        return None
    text = lint_file.read_text(encoding="utf-8")
    before = yaml.safe_load(text).get("linters")
    lines = text.splitlines(keepends=True)
    heading = next((n for n, line in enumerate(lines) if _LINTERS.match(line)), None)
    if heading is not None:
        indent = _indent(lines[heading + 1 :])
        lines.insert(heading + 1, f"{indent}{key}:\n{indent * 2}rules: {relative}\n")
        updated = "".join(lines)
    elif before is None:
        ending = "" if text.endswith("\n") else "\n"
        updated = f"{text}{ending}linters:\n  {key}:\n    rules: {relative}\n"
    else:
        updated = text
    after = yaml.safe_load(updated).get("linters")
    if after != {**(before or {}), key: {"rules": relative}}:
        raise UntapedError(
            f"{lint_file}: can't add linters.{key} to this layout; add it yourself: "
            f"linters: {{{key}: {{rules: {relative}}}}}",
            category=ErrorCategory.CONFLICT,
            system="local",
        )
    return updated, False


_LINTERS = re.compile(r"^linters:[ \t]*(#.*)?$")


def _indent(lines: list[str]) -> str:
    """The indentation of the first entry under ``linters:``, two spaces when there is none."""
    for line in lines:
        if line.strip() and not line.lstrip().startswith("#"):
            stripped = line.lstrip(" ")
            return " " * (len(line) - len(stripped)) if len(stripped) < len(line) else "  "
    return "  "


def _module(rule_id: str, key: str) -> str:
    name = _python_name(rule_id)
    return f'''"""{key}.{rule_id}: say the convention in one sentence, then write its check."""

from collections.abc import Iterator

from untaped_lint.api import FileLocation, Finding, Tree, finding, rule, target_of


@rule(
    "{rule_id}",
    reads=Tree,
    severity="error",
    summary="The convention in one sentence, as the next agent reads it.",
)
def {name}(tree: Tree) -> Iterator[Finding]:
    """One finding for each line of the repository's files that breaks the convention."""
    for file in tree.files("**/*"):  # narrow the glob to the files the convention is about
        for number, line in enumerate(file.lines, start=1):
            if _breaks(line):
                yield finding(
                    {name},
                    target_of(file),
                    "what is wrong, and how to fix it",
                    location=FileLocation(path=file.path, line=number),
                )


def _breaks(line: str) -> bool:
    """Whether ``line`` breaks the convention: write this check after the examples."""
    return False
'''


def _test(rule_id: str, key: str) -> str:
    name = _python_name(rule_id)
    return f'''"""{key}.{rule_id}: the bad example breaks the rule and the good one doesn't."""

from pathlib import Path

from untaped_lint.testing import repo_rule, run_rule, tree

RULE = repo_rule("{rule_id}", root=Path(__file__).resolve().parents[2])

# Write these first: the smallest files that break the convention, and follow it.
BAD = {{"example.txt": "a file that breaks the convention\\n"}}
GOOD = {{"example.txt": "a file that follows it\\n"}}


def test_{name}_flags_the_bad_example() -> None:
    assert [each.rule for each in run_rule(RULE, tree(BAD))] == ["{rule_id}"]


def test_{name}_passes_the_good_example() -> None:
    assert run_rule(RULE, tree(GOOD)) == []
'''


_BAD = "Replace this with files that break the convention.\n"
_GOOD = "Replace this with files that follow the convention.\n"
