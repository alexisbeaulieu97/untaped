"""``untaped lint``: rules over the plugin repository it runs in, or records piped in."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from cyclopts import Parameter

from untaped.sdk import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    StdinOption,
    UsageError,
    create_app,
    emit,
    read_stdin_input,
    report_errors,
    writes,
)
from untaped_lint.cli.options import IgnoreOption, SelectOption, StrictOption

if TYPE_CHECKING:
    from untaped_lint.config import LintConfig

app = create_app(
    name="lint", help="Check plugins, repositories and records against rules, with ids to ignore."
)

ConfigOption = Annotated[
    Path | None,
    Parameter(
        name="--config",
        help="Read this lint file instead of .untaped/lint.yml, and lint the current directory.",
    ),
]


@app.default
def lint_command(
    paths: Annotated[
        list[str] | None,
        Parameter(help="Report only findings in files matching these globs (** crosses /)."),
    ] = None,
    /,
    *,
    stdin: StdinOption = False,
    select: SelectOption = None,
    ignore: IgnoreOption = None,
    strict: StrictOption = False,
    config: ConfigOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Check the repository you are in, or records piped with --stdin.

    The plugins its pyproject.toml declares are checked (conventions,
    contract fills, file rules), and its files against every rule that reads
    them, the rules of .untaped/lint.yml included. Exit 3 when a rule
    reports an error, 1 when a rule could not run.
    """
    from dataclasses import replace  # noqa: PLC0415

    from untaped.sdk import installed_plugins  # noqa: PLC0415
    from untaped_lint.application.output import lint  # noqa: PLC0415
    from untaped_lint.application.subjects import (  # noqa: PLC0415
        declared_plugins,
        repository_subjects,
        stdin_subjects,
    )
    from untaped_lint.domain.selection import parse_rule, parse_suppression  # noqa: PLC0415

    with report_errors():
        for each in select or ():  # a usage mistake is one wherever it runs
            parse_rule(each, option="--select")
        for each in ignore or ():
            parse_suppression(each, option="--ignore")
        if stdin and paths:
            raise UsageError("--stdin reads records, so it takes no PATHS")
        settings = _lint_config(Path.cwd(), config=config)
        if stdin:
            piped = read_stdin_input(what="records")
            if piped.records is None:
                raise UsageError("--stdin reads records: pipe untaped's --format pipe output")
            subjects, findings = stdin_subjects(piped.records), []
            settings = replace(settings, tree_root=None)
        else:
            found = repository_subjects(declared_plugins(Path.cwd()), installed_plugins())
            subjects, findings = found.subjects, found.findings
        lint(
            subjects,
            config=settings,
            select=select or (),
            ignore=ignore or (),
            paths=paths or (),
            strict=strict,
            findings=findings,
            fmt=fmt,
            columns=columns,
        )


@app.command(name="rules")
def rules_command(
    *, config: ConfigOption = None, fmt: FormatOption = "table", columns: ColumnsOption = None
) -> None:
    """List every rule: lint's own, each plugin's and the repository's own (.untaped/lint.yml)."""
    from untaped.sdk import Diagnostics  # noqa: PLC0415
    from untaped_lint.application.engine import loaded_rules  # noqa: PLC0415
    from untaped_lint.domain.report import Report  # noqa: PLC0415

    with report_errors():
        report = Report()
        repo = _lint_config(Path.cwd(), config=config).repo
        rules = loaded_rules(report, repo=repo)
        diag = Diagnostics()
        for failure in report.failures:
            diag.failure(failure)
        rows = [rule.model_copy(update={"id": public}) for public, rule in sorted(rules.items())]
        emit(rows, fmt=fmt, columns=columns)


@app.command(name="new")
@writes
def new_command(
    id: Annotated[str, Parameter(help="The rule's bare id, as no-print.")],
    /,
    *,
    key: Annotated[
        str | None,
        Parameter(
            name="--key",
            help="The repository key the rule is under (default: the lint file's name:).",
        ),
    ] = None,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Scaffold a repository rule in .untaped/lint/ and its test, fixtures first.

    The rule reads the repository's files (reads=Tree). With a pytest
    configuration at the root its test goes in tests/lint/; otherwise a
    good and a bad fixture directory, checked by the two commands printed.
    The lint file and its linters entry are created when missing.
    """
    from untaped.sdk import Diagnostics, installed_plugins  # noqa: PLC0415
    from untaped_lint.application.scaffold import scaffold  # noqa: PLC0415

    with report_errors():
        installed = [plugin.name for plugin in installed_plugins()]
        outcome = scaffold(Path.cwd(), id, key=key, installed=installed, dry_run=dry_run)
        emit(outcome.files, fmt=fmt, columns=columns)
        for line in outcome.next_steps:
            Diagnostics().info(line)


def _lint_config(start: Path, *, config: Path | None) -> LintConfig:
    """The profile's ``lint.ignore`` and the repo file found from ``start``.

    ``config`` reads that file instead of the one found walking up, and the
    tree is then ``start`` itself (the file says which rules, you say
    where); otherwise the tree is the found file's root, or the project
    root of ``start`` without one.
    """
    from untaped.sdk import get_config_section, installed_plugins, project_root  # noqa: PLC0415
    from untaped_lint.application import repo_file  # noqa: PLC0415
    from untaped_lint.config import LintConfig  # noqa: PLC0415
    from untaped_lint.settings import LintSettings  # noqa: PLC0415

    settings = get_config_section("lint", LintSettings)
    path = config if config is not None else repo_file.find(start)
    repo = None
    if path is not None:
        installed = [plugin.name for plugin in installed_plugins()]
        repo = repo_file.read(path, installed=installed)
    if config is not None:
        root = start.resolve()
    else:
        root = repo.root if repo is not None else project_root(start)
    return LintConfig(ignore=tuple(settings.ignore), repo=repo, tree_root=root)
