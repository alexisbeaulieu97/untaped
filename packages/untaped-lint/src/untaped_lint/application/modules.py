"""A repository's own rules: the modules in a ``rules:`` directory, as one host per key.

Each ``*.py`` directly in the directory (not its ``fixtures/``) is imported
in-process, the way ``pytest`` imports a checkout's ``conftest.py``: no
sandbox and no prompt. Its ``@rule`` functions make one :class:`Rules` host,
which the engine composes under the file's key (``house.no-print``).

Before a module is imported, ``lint.rule-imports`` reads it: a rule module
imports the standard library, ``untaped_lint.api`` and an installed
plugin's ``untaped_<plugin>.api``, nothing else, because it runs in
untaped's environment and not the repository's. An ``untaped_<plugin>``
import whose plugin isn't installed fails the load (exit 4) naming the extra.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from untaped.sdk import ConfigError, ErrorCategory, Failure
from untaped_lint.application.repo_file import RepoFile
from untaped_lint.application.rules import Rules
from untaped_lint.records import FileLocation, Finding, Rule, Tree, declared_kind

TREE_KIND = declared_kind(Tree)
RULE_IMPORTS = Rule(
    id="rule-imports",
    reads=TREE_KIND,
    severity="error",
    summary=(
        "A repository rule module imports only the standard library, untaped_lint.api and an "
        "installed plugin's untaped_<plugin>.api."
    ),
)
_API = re.compile(r"^untaped_[a-z0-9_]+\.api$")
_PLUGIN_PACKAGE = re.compile(r"^untaped_(?P<plugin>[a-z0-9_]+)$")
_MODULE_PREFIX = "untaped_lint_repo_rules"


@dataclass
class Loaded:
    """The hosts a repo file's ``rules:`` directories make, and what loading them found."""

    hosts: dict[str, Rules] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    """``rule-imports`` findings, ids bare (lint's key is the engine's to add)."""
    failures: list[Failure] = field(default_factory=list)
    """One per module that raised on import: its key's rules from it did not load."""


def load(repo: RepoFile) -> Loaded:
    """Import every key's rule modules; a missing plugin import raises :class:`ConfigError`."""
    loaded = Loaded()
    for key, directory in repo.rules.items():
        if not directory.is_dir():
            raise ConfigError(
                f"{repo.path}: linters.{key}.rules: {directory} is not a directory",
                category="invalid",
            )
        modules: list[ModuleType] = []
        for path in sorted(directory.glob("*.py")):
            relative = _relative(path, repo.root)
            try:
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text, filename=str(path))
            except (UnicodeDecodeError, SyntaxError) as exc:
                loaded.failures.append(_failure(key, relative, exc))
                continue
            loaded.findings.extend(rule_import_findings(tree, relative))
            module = _import(key, path, relative, loaded)
            if module is not None:
                modules.append(module)
        loaded.hosts[key] = Rules.from_module(*modules)
    return loaded


def rule_import_findings(tree: ast.Module, path: str) -> list[Finding]:
    """``rule-imports`` findings for one rule module's syntax tree at ``path``."""
    found: list[Finding] = []
    for node in ast.walk(tree):
        for target in _imported(node):
            if not _allowed(target):
                found.append(
                    Finding(
                        rule=RULE_IMPORTS.id,
                        severity=RULE_IMPORTS.severity,
                        target=path,
                        message=(
                            f"imports {target}; a rule module imports only the standard library, "
                            "untaped_lint.api and an installed plugin's api (a rule that needs "
                            "more belongs in a plugin)"
                        ),
                        location=FileLocation(path=path, line=getattr(node, "lineno", None)),
                    )
                )
    return found


def _imported(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return ["." * node.level + (node.module or "")]
        return [node.module or ""]
    return []


def _allowed(target: str) -> bool:
    top = target.split(".", 1)[0]
    if target.startswith("."):
        return False
    if top == "__future__" or top in sys.stdlib_module_names:
        return True
    return target == "untaped_lint.api" or bool(_API.match(target))


def _import(key: str, path: Path, relative: str, loaded: Loaded) -> ModuleType | None:
    name = f"{_MODULE_PREFIX}.{key.replace('-', '_')}.{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - a .py path always has one
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    writes_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no __pycache__ left in the repository's rules directory
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:
        sys.modules.pop(name, None)
        top = (exc.name or "").split(".", 1)[0]
        plugin = _PLUGIN_PACKAGE.match(top)
        if plugin is not None and importlib.util.find_spec(top) is None:
            extra = plugin["plugin"].replace("_", "-")
            raise ConfigError(
                f"{relative} imports {exc.name}, which is not installed; install untaped[{extra}]"
            ) from None
        loaded.failures.append(_failure(key, relative, exc))
        return None
    except Exception as exc:  # a module that raises loses its own rules, not the run
        sys.modules.pop(name, None)
        loaded.failures.append(_failure(key, relative, exc))
        return None
    finally:
        sys.dont_write_bytecode = writes_bytecode
    return module


def _failure(key: str, relative: str, error: BaseException) -> Failure:
    return Failure(
        category=ErrorCategory.FAILED,
        system=key,
        item=key,
        message=f"{key}'s rules in {relative} did not load: {error}",
        hint="untaped lint rules",
        details={"reason": "raised"},
    )


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
