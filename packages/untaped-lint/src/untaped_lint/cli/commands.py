"""``untaped lint``: rules over the plugin repository it runs in, or records piped in."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

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
from untaped_lint.cli.lint_config import lint_config
from untaped_lint.cli.options import IgnoreOption, SelectOption, StrictOption

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

    The plugins its nearest pyproject.toml declares are checked (house
    conventions, contract fills, the rules that read their files); each must
    be installed in this environment, as with ``uv pip install -e .``, or it
    is the lint.plugin-not-installed finding. Its files are checked against
    every rule that reads them, the rules .untaped/lint.yml names included.
    Findings go to stdout as lint.finding rows and a count to stderr.

    Exit 3 when a finding is an error (any finding with --strict), 1 when a
    rule or provider could not run, 2 on a usage mistake or a lint file key
    that could shadow a plugin, 4 on a configuration problem such as a rule
    importing a plugin that isn't installed; the most severe wins. Reading
    findings and leaving one out: ``untaped agents lint findings``.

    Examples
    --------
    ```console
    $ untaped lint
    $ untaped lint 'src/**/cli/*.py'
    $ untaped lint --select house.no-print
    $ untaped lint --ignore 'house.no-print:tests/**'
    $ untaped lint --strict
    ```

    Every installed plugin:

    ```console
    $ untaped plugin list -f pipe | untaped lint --stdin
    ```
    """
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
        settings = lint_config(Path.cwd(), config=config, tree=not stdin)
        if stdin:
            piped = read_stdin_input(what="records")
            if piped.records is None:
                raise UsageError("--stdin reads records: pipe untaped's --format pipe output")
            subjects, findings = stdin_subjects(piped.records), []
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
    """List every rule: lint's own, each plugin's and the repository's own (.untaped/lint.yml).

    Each row is a rule's public id, the subject it reads, its severity and its
    summary: the convention in one sentence.
    """
    from untaped.sdk import Diagnostics  # noqa: PLC0415
    from untaped_lint.application.engine import loaded_rules  # noqa: PLC0415
    from untaped_lint.domain.report import Report  # noqa: PLC0415

    with report_errors():
        report = Report()
        repo = lint_config(Path.cwd(), config=config).repo
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
    The lint file and its linters entry are created when missing. How a
    rule is written: ``untaped agents lint writing-rules``.

    Examples
    --------
    ```console
    $ untaped lint new no-print --dry-run
    $ untaped lint new no-print --key house
    ```
    """
    from untaped.sdk import Diagnostics, installed_plugins  # noqa: PLC0415
    from untaped_lint.application.scaffold import scaffold  # noqa: PLC0415

    with report_errors():
        installed = [plugin.name for plugin in installed_plugins()]
        outcome = scaffold(Path.cwd(), id, key=key, installed=installed, dry_run=dry_run)
        emit(outcome.files, fmt=fmt, columns=columns)
        for line in outcome.next_steps:
            Diagnostics().info(line)
