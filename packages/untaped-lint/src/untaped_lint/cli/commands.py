"""``untaped lint``: rules over the plugin repository it runs in, or records piped in."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from untaped.sdk import (
    ColumnsOption,
    FormatOption,
    StdinOption,
    UsageError,
    create_app,
    emit,
    get_config_section,
    read_stdin_input,
    report_errors,
)
from untaped_lint.cli.options import IgnoreOption, SelectOption, StrictOption
from untaped_lint.settings import LintSettings

app = create_app(name="lint", help="Check plugins and records against rules, with ids to ignore.")


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
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Check the plugin repository you are in, or records piped with --stdin.

    In a plugin repository the plugins its pyproject.toml declares are
    checked: the conventions, contract fills and file rules. Exit 3 when a
    rule reports an error, 1 when a rule could not run.
    """
    from untaped.sdk import Diagnostics, installed_plugins  # noqa: PLC0415
    from untaped_lint.application.output import lint  # noqa: PLC0415
    from untaped_lint.application.subjects import (  # noqa: PLC0415
        declared_plugins,
        repository_subjects,
        stdin_subjects,
    )
    from untaped_lint.config import LintConfig  # noqa: PLC0415
    from untaped_lint.domain.selection import parse_rule, parse_suppression  # noqa: PLC0415

    with report_errors():
        settings = get_config_section("lint", LintSettings)
        for each in select or ():  # a usage mistake is one wherever it runs
            parse_rule(each, option="--select")
        for each in ignore or ():
            parse_suppression(each, option="--ignore")
        if stdin:
            if paths:
                raise UsageError("--stdin reads records, so it takes no PATHS")
            piped = read_stdin_input(what="records")
            if piped.records is None:
                raise UsageError("--stdin reads records: pipe untaped's --format pipe output")
            subjects, findings = stdin_subjects(piped.records), []
        else:
            found = repository_subjects(declared_plugins(Path.cwd()), installed_plugins())
            subjects, findings = found.subjects, found.findings
            if not subjects and not findings:
                Diagnostics().warning(
                    "nothing to lint here: run it in a plugin repository or pipe records "
                    "with --stdin"
                )
                return
        lint(
            subjects,
            config=LintConfig(ignore=tuple(settings.ignore)),
            select=select or (),
            ignore=ignore or (),
            paths=paths or (),
            strict=strict,
            findings=findings,
            fmt=fmt,
            columns=columns,
        )


@app.command(name="rules")
def rules_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List every rule installed: lint's own conventions and each plugin's."""
    from untaped.sdk import Diagnostics  # noqa: PLC0415
    from untaped_lint.application.engine import loaded_rules  # noqa: PLC0415
    from untaped_lint.domain.report import Report  # noqa: PLC0415

    with report_errors():
        report = Report()
        rules = loaded_rules(report)
        diag = Diagnostics()
        for failure in report.failures:
            diag.failure(failure)
        rows = [rule.model_copy(update={"id": public}) for public, rule in sorted(rules.items())]
        emit(rows, fmt=fmt, columns=columns)
