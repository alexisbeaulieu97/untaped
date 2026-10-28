"""``untaped lint``: rules over the plugin repository it runs in, or records piped in."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, Field

from untaped.sdk import (
    Command,
    DryRun,
    Matched,
    Output,
    Positional,
    Record,
    Stdin,
    UsageError,
)
from untaped_lint.commands._lint import Findings, LintCommand
from untaped_lint.records import Finding, Rule, ScaffoldOutcome
from untaped_lint.settings import LintSettings


class Lint(LintCommand):
    """Check the repository you are in, or records piped with --stdin.

    The plugins its nearest pyproject.toml declares are checked (house
    conventions, contract fills, the rules that read their files); each must
    be installed in this environment, as with `uv pip install -e .`, or it
    is the lint.plugin-not-installed finding. Its files are checked against
    every rule that reads them, the rules .untaped/lint.yml names included.
    Findings go to stdout as lint.finding rows and a count to stderr.

    Exit 3 when a finding is an error (any finding with --strict), 1 when a
    rule or provider could not run or the lint file is invalid, 2 on a usage
    mistake or a lint file key that could shadow a plugin, 4 on a
    configuration problem such as a rule importing a plugin that isn't
    installed; the most severe wins (2, then 4, then 1, then 3).

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

    paths: Positional[list[str]] = Field(
        default_factory=list,
        description="Report only findings in files matching these globs (** crosses /).",
    )
    stdin: Stdin[Record]
    findings: Findings = Field(default_factory=Findings)
    output: Output[Finding]

    def run(self) -> Iterator[Finding]:
        from untaped_lint.application.output import lint_report  # noqa: PLC0415 - the engine
        from untaped_lint.application.subjects import (  # noqa: PLC0415 - the engine
            declared_plugins,
            repository_subjects,
            stdin_subjects,
        )

        self.findings.check()  # a usage mistake is one wherever it runs
        if self.stdin.requested and self.paths:
            raise UsageError("--stdin reads records, so it takes no PATHS")
        config = self.lint_config(tree=not self.stdin.requested)
        if self.stdin.requested:
            subjects, made = stdin_subjects(self._piped()), []
        else:
            found = repository_subjects(declared_plugins(Path.cwd()), self.ctx.plugins)
            subjects, made = found.subjects, found.findings
        report = lint_report(
            subjects,
            config=config,
            select=self.findings.select,
            ignore=self.findings.ignore,
            paths=self.paths,
            findings=made,
        )
        for warning in report.warnings:
            self.ctx.diag.warning(warning)
        for failure in report.failures:
            self.ctx.diag.failure(failure)
        yield from report.findings
        self.ctx.diag.info(report.summary())
        if report.predicate_hit(strict=self.findings.strict):
            raise Matched

    def _piped(self) -> Iterator[tuple[str | None, Record]]:
        for value in self.stdin.values():
            if isinstance(value, str):
                raise UsageError("--stdin reads records: pipe untaped's --format pipe output")
            yield self.stdin.kind(value), value


class Rules(LintCommand):
    """List every rule: lint's own, each plugin's and the repository's own (.untaped/lint.yml).

    Each row is a rule; the table shows its id, what it reads, its severity
    and its summary, the convention in one sentence.
    """

    output: Output[Rule]

    def run(self) -> Iterator[Rule]:
        from untaped_lint.application.engine import loaded_rules  # noqa: PLC0415 - the engine
        from untaped_lint.domain.report import Report  # noqa: PLC0415 - the engine

        report = Report()
        rules = loaded_rules(report, repo=self.lint_config().repo)
        for failure in report.failures:
            self.ctx.diag.failure(failure)
        for public, rule in sorted(rules.items()):
            yield rule.model_copy(update={"id": public})


class RuleModule(BaseModel):
    """How a convention becomes a repository rule: the rule module and its test.

    Read before turning a convention into a repository rule.

    A rule reads one subject (the repository's files, a plugin, a plugin's
    records) and yields findings.

    ## How a rule comes to be

    1. The convention, said in one sentence, is the rule's `summary`: what
       `untaped lint rules` shows the next agent.
    2. `untaped lint new ID` (a kebab-case id, as `no-print`) writes the rule
       module and its test, and the lint file when the repository has none
       (`untaped lint new --help` says where each goes).
    3. The test holds the smallest `BAD` files that break the convention and
       `GOOD` ones that follow it; it fails until the rule is written.
    4. `untaped lint` at the root then reports what the rule finds in the code.

    ## A rule

    ```python
    # .untaped/lint/no_print.py
    \"\"\"house.no-print: library code writes through the CLI's output, never print().\"\"\"

    from collections.abc import Iterator

    from untaped_lint.api import FileLocation, Finding, Tree, finding, rule, target_of


    @rule("no-print", reads=Tree, severity="error", summary="Library code never calls print().")
    def no_print(tree: Tree) -> Iterator[Finding]:
        for file in tree.files("src/**/*.py"):
            for number, line in enumerate(file.lines, start=1):
                if "print(" in line:
                    yield finding(
                        no_print,
                        target_of(file),
                        "print() in library code; return the value instead",
                        location=FileLocation(path=file.path, line=number),
                    )
    ```

    - `reads=Tree` takes the model, never a kind string.
    - `tree.files(*globs)` yields each matching file: `path`, `text`, `lines`,
      and `json()`, `yaml()`, `python()` for the parsed file; in a glob `*` stops
      at `/` and `**` crosses it. A file that isn't
      UTF-8 never reaches the rule; lint reports it as `lint.unreadable-file`.
    - `target_of(file)` is the file's path, which an ignore glob matches.
    - The id is bare: the run prefixes the key, so the finding reads
      `house.no-print`.

    ## Its test

    ```python
    # tests/lint/test_no_print.py
    from pathlib import Path

    from untaped_lint.testing import repo_rule, run_rule, tree

    RULE = repo_rule("no-print", root=Path(__file__).resolve().parents[2])

    BAD = {"src/a.py": "print(1)\\n"}
    GOOD = {"src/a.py": "x = 1\\n", "scripts/b.py": "print(1)\\n"}


    def test_no_print_flags_the_bad_example() -> None:
        assert [each.rule for each in run_rule(RULE, tree(BAD))] == ["no-print"]


    def test_no_print_passes_the_good_example() -> None:
        assert run_rule(RULE, tree(GOOD)) == []
    ```

    What a rule module may import, and what loading it runs:
    `untaped docs lint inputs lint_file`.
    """

    key: str | None = Field(
        None, description="The repository key the rule is under (default: the lint file's name:)."
    )


class New(Command[LintSettings]):
    """Scaffold a repository rule in .untaped/lint/ and its test, fixtures first.

    The rule reads the repository's files (reads=Tree). With a pytest
    configuration at the root its test goes in tests/lint/; otherwise
    fixtures/ID/good and fixtures/ID/bad under the rules directory, checked
    by the two commands printed. The lint file and its linters entry are
    created when missing; without a lint file, --key names its key.

    Examples
    --------
    ```console
    $ untaped lint new no-print --dry-run
    $ untaped lint new no-print --key house
    ```
    """

    id: Positional[str] = Field(description="The rule's bare id, as no-print.")
    module: RuleModule = Field(default_factory=RuleModule)
    dry_run: DryRun
    output: Output[ScaffoldOutcome]

    def run(self) -> Iterator[ScaffoldOutcome]:
        from untaped_lint.application.scaffold import scaffold  # noqa: PLC0415 - writes only

        installed = [plugin.name for plugin in self.ctx.plugins]
        outcome = scaffold(
            Path.cwd(),
            self.id,
            key=self.module.key,
            installed=installed,
            dry_run=bool(self.dry_run),
        )
        yield from outcome.files
        for line in outcome.next_steps:
            self.ctx.diag.info(line)
