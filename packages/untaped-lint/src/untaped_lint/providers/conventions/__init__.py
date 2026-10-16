"""Lint's own plugin linter: core's composed checks and the file rules, as findings.

Core keeps the checks only the composed root can answer behind one door,
:func:`untaped.testing.check_plugin`, which returns rows with check names
and a status; this module turns them into findings (``fail`` an error,
``warn`` a warning). The rules that read only the plugin's files live here.
The engine calls :class:`Conventions` directly, before it asks any provider:
``check_plugin`` composes, and no contract method may.

Every id is in :data:`RULES`, so ``untaped lint rules`` lists what ran.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from untaped.sdk import Plugin, UsageError, kind_of
from untaped.testing import PluginCandidate, PluginCheckRow, check_plugin
from untaped_lint.errors import UnreadableSourceError
from untaped_lint.infrastructure.files import SourceFile, source_files
from untaped_lint.providers.conventions.blob_reader import blob_reader_violations
from untaped_lint.providers.conventions.layering import layering_violations
from untaped_lint.providers.conventions.messages import message_violations
from untaped_lint.providers.conventions.store_format import store_format_violations
from untaped_lint.providers.conventions.structure import structure_source_violations
from untaped_lint.providers.conventions.terminal_boundary import terminal_boundary_violations
from untaped_lint.records import FileLocation, Finding, Rule, Severity

KEY = "lint"
"""The key of every Conventions id: ``lint.error-case``."""

_PLUGIN = kind_of(Plugin) or "untaped.plugin"

_TABLE: tuple[tuple[str, Severity, str], ...] = (
    # The composed checks: names, requirements, the help tree, marks, structure, imports.
    ("plugin-name", "error", "A plugin's distribution and import package are named after it."),
    ("provides-requirement", "error", "A provider requires untaped and each owner with a range."),
    ("missing-help", "error", "Every visible parameter has help text."),
    ("duplicate-option", "error", "No two parameters of one command answer to one name."),
    ("empty-negative", "error", "No --empty-* negative flag is exposed."),
    ("needless-negative", "error", "A boolean that defaults to false has no --no-* flag."),
    ("positional-or-keyword", "error", "A parameter is positional or an option, not both."),
    ("command-name", "error", "Command and group names are kebab-case."),
    ("reserved-short", "error", "A reserved short flag keeps its core meaning."),
    ("ambiguous-short", "error", "A short flag means one thing within a plugin."),
    ("undeclared-write", "error", "A command exposing --yes or --dry-run declares @writes."),
    ("mutation-format", "error", "A declared write has --format."),
    ("destructive-controls", "error", "A destructive command has --yes and --dry-run."),
    ("reserved-panel", "error", "No help panel is named after one of core's panels."),
    ("unkeyed-panel", "error", "A plugin's own help panel has a sort key below 100."),
    ("hand-typed-mark", "error", "Experimental and Deprecated lines come from marks, not help."),
    ("wrong-deprecated", "error", "Commands and settings deprecate with untaped's deprecated()."),
    ("nested-mark", "error", "No mark sits under a mark that makes it redundant."),
    ("bad-replacement", "error", "A deprecation's replacement resolves and is not deprecated."),
    ("mark-on-spec", "error", "A plugin's own mark is on its PluginSpec, not its top app."),
    ("exception-base", "error", "A plugin's exceptions derive from UntapedError."),
    ("exception-name", "error", "A plugin's exception classes end in Error."),
    ("error-system", "error", "A plugin's error classes name the system responsible."),
    ("protocol-location", "error", "Ports (Protocols) live in the domain layer."),
    ("port-adapter-clash", "error", "A port and an adapter never share a name."),
    ("settings-not-frozen", "error", "Settings models are frozen."),
    ("settings-naming", "error", "Setting names follow the naming rules (_dir, _path, parallel)."),
    ("settings-renames", "error", "Renamed setting keys are declared consistently."),
    ("import-boundary", "error", "A plugin imports core and other plugins only by public modules."),
    # The file rules: stderr wording, structure, layering, boundaries and stores, read from files.
    ("echo-error", "error", "Errors are raised as UntapedError, never echoed."),
    ("echo-warning", "error", "Warnings go through ctx.diag.warning, never echo."),
    ("echo-failed", "error", "Per-item failures go through ctx.diag.failure, never echo."),
    ("warnings-warn", "error", "User warnings go through diagnostics, not warnings.warn."),
    ("capital-warning", "error", "No literal starts with Warning:."),
    ("paren-s", "error", "Plurals use plural(), never a parenthesised s."),
    ("error-case", "error", "Error messages start lowercase and end without a period."),
    ("usage-phrase", "error", "Usage mistakes raise UsageError."),
    ("sys-stdin", "error", "Stdin is read through core's stdin helpers."),
    ("local-plural", "error", "Plurals use core's plural(), not a local helper."),
    ("print", "error", "Stdout goes through echo or UiContext, stderr through ctx.diag."),
    ("rich-console", "error", "No direct rich Console: use echo, UiContext or ctx.diag."),
    ("hand-typed-deprecation", "error", "Deprecation sentences come from marks, not literals."),
    ("errors-module", "error", "A plugin keeps its error classes in errors.py."),
    ("foreign-section", "error", "A plugin reads only its own config section."),
    ("private-test-import", "error", "Tests import no private module or name."),
    ("layer", "error", "Layers point one way: cli, application, domain; infrastructure, domain."),
    ("settings", "error", "Only the cli layer resolves settings."),
    ("terminal-boundary", "error", "No prompt_toolkit import: build screens with the SDK."),
    ("blob-reader", "error", "Repo store blobs are read through store.prefetched(...).run()."),
    ("store-format", "error", "A database under plugin_dir() is opened through a Store."),
    ("unreadable-file", "error", "Every file a rule reads is UTF-8; a plugin's Python parses."),
    # The contract checks and the plugin itself.
    ("plugin-not-installed", "error", "A plugin its pyproject.toml declares is installed."),
    ("registration", "error", "The registry accepts every offer a plugin makes."),
    ("quarantined", "error", "Composition accepts the plugin."),
    ("contracts", "error", "An owner's contracts function loads."),
    ("conformance", "error", "Each ready provider passes its owner's conformance checks."),
    ("live", "error", "Each filled method taking no arguments answers with valid items."),
    ("fills", "error", "A provider's live items pass assert_fills."),
    ("schema", "warning", "A provider records the owner schema hash it was tested against."),
    ("owner-schema-drift", "warning", "A provider was tested against the installed owner."),
    ("migrations", "error", "Each directory migration previews on an empty HOME."),
)

RULES: tuple[Rule, ...] = tuple(
    Rule(id=id_, reads=_PLUGIN, severity=severity, summary=summary)
    for id_, severity, summary in _TABLE
)
_BY_ID = {rule.id: rule for rule in RULES}
UNREADABLE = _BY_ID["unreadable-file"]
"""Also a ``Tree``'s: a file a rule asked for that isn't UTF-8."""

_FILE_WHERE = re.compile(r"^(?P<path>[^\s:]+\.py)(?::(?P<line>\d+))?$")
_DRIFT = "owner-schema-drift: "


UNCHECKED = frozenset(
    {f"{KEY}.plugin-not-installed", f"{KEY}.quarantined", f"{KEY}.unreadable-file"}
)
"""Findings meaning rules did not run on the plugin, so a narrowed run still reports them."""


class Conventions:
    """The rules over an ``untaped.plugin`` subject; the engine's own code, never a provider."""

    def rules(self) -> list[Rule]:
        return list(RULES)

    def findings(
        self, plugin: Plugin, *, candidates: Sequence[PluginCandidate] | None = None
    ) -> list[Finding]:
        """Every finding for ``plugin``; ``candidates`` replaces entry-point discovery."""
        try:
            rows = check_plugin(plugin.name, candidates=candidates)
        except UsageError:
            return [not_installed(plugin.name)]
        source_dir = None if plugin.source_dir is None else Path(plugin.source_dir)
        tests_dir = None if plugin.tests_dir is None else Path(plugin.tests_dir)
        code = None if source_dir is None else source_dir.parent
        if rows and all(row.check == "registration" for row in rows):
            # Composition refused the plugin (a composed one always has its
            # conventions row): its one row says why; nothing else ran.
            rows = [row.model_copy(update={"check": "quarantined"}) for row in rows]
        found = [
            finding for row in rows if (finding := _from_row(plugin.name, row, code)) is not None
        ]
        unreadable = any(finding.rule == "unreadable-file" for finding in found)
        if plugin.package is not None and source_dir is not None and not unreadable:
            lines = file_lines(plugin.package, source_dir, plugin=plugin.name, tests_dir=tests_dir)
            for line in lines:
                tests = line.split("::")[1] == "private-test-import"
                base = tests_dir.parent if tests and tests_dir is not None else code
                found.append(_from_line(plugin.name, line, base))
        return found


def file_lines(
    package: str, source_dir: Path, *, plugin: str, tests_dir: Path | None = None
) -> list[str]:
    """The file rules' ``<where>::<rule>::<detail>`` lines for ``package`` in ``source_dir``.

    Core's deprecated ``untaped plugin check`` and ``check_conventions`` read
    these lines to keep their rows unchanged until they are removed. An unreadable file is
    one ``unreadable-file`` line and the other rules don't run.
    """
    try:
        files: list[SourceFile] = list(source_files(source_dir))
        found = [
            *message_violations(source_dir, files),
            *structure_source_violations(plugin, package, source_dir, files, tests_dir=tests_dir),
            *layering_violations(package, source_dir, files),
            *terminal_boundary_violations(source_dir, files),
            *blob_reader_violations(package, source_dir, files),
            *store_format_violations(plugin, source_dir, files),
        ]
    except UnreadableSourceError as exc:
        where = _relative(exc.path, source_dir.parent)
        return [f"{where}::unreadable-file::{exc.reason}"]
    return sorted(found)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _from_row(plugin: str, row: PluginCheckRow, base: Path | None) -> Finding | None:
    if row.status == "pass":
        return None
    severity: Severity = "warning" if row.status == "warn" else "error"
    if row.check == "conventions":
        where, _, detail = row.detail.partition(": ")
        return _finding(row.title, plugin, where, detail, severity, base)
    if row.check == "schema" and row.detail.startswith(_DRIFT):
        detail = f"{row.title}: {row.detail.removeprefix(_DRIFT)}"
        return _finding("owner-schema-drift", plugin, "", detail, severity, base)
    return _finding(row.check, plugin, "", f"{row.title}: {row.detail}", severity, base)


def _from_line(plugin: str, line: str, base: Path | None) -> Finding:
    where, rule, detail = [*line.split("::", 2), "", ""][:3]
    declared = _BY_ID.get(rule)
    severity: Severity = "error" if declared is None else declared.severity
    return _finding(rule, plugin, where, detail, severity, base)


def _finding(
    rule: str, plugin: str, where: str, detail: str, severity: Severity, base: Path | None
) -> Finding:
    """A finding about ``plugin``; a ``where`` naming a file becomes its location."""
    match = _FILE_WHERE.match(where)
    location = None
    message = f"{where}: {detail}" if where and detail else where or detail
    if match is not None:
        line = match["line"]
        path = _shown(match["path"], base)
        location = FileLocation(path=path, line=None if line is None else int(line))
        declared = _BY_ID.get(rule)
        message = detail or (declared.summary if declared is not None else rule)
    return Finding(rule=rule, severity=severity, target=plugin, message=message, location=location)


def _shown(path: str, base: Path | None) -> str:
    """``path`` (relative to ``base``) as the user reads it: from the cwd when inside it."""
    if base is None:
        return path
    absolute = (base / path).resolve()
    try:
        return absolute.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return absolute.as_posix()


def not_installed(name: str) -> Finding:
    """The finding for plugin ``name``, which nothing in this environment installs."""
    return Finding(
        rule="plugin-not-installed",
        severity="error",
        target=name,
        message=f"{name} is not installed in this environment",
        hint="install the project here, as in `uv pip install -e .`",
    )
