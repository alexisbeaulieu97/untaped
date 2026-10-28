# Lint

`untaped lint` (the `lint` plugin, `untaped[lint]`, experimental) runs rules
over subjects and reports findings. Every plugin's rules share one id
spelling, one way to leave a finding out and one set of exit codes, so an
agent or a CI job reads them all the same way.

## Checking a plugin

In a plugin repository, `untaped lint` reads the nearest `pyproject.toml`
(up to the repository root) and checks each plugin it declares under
`[project.entry-points."untaped.plugins"]`. The plugin must be installed in
the same environment (`uv pip install -e .`); one that isn't is the
`lint.plugin-not-installed` finding.

Each plugin is checked against:

- what core can only tell from the composed root: names, the provider's
  requirements, the help tree, guides and overview lines, stability marks, error
  and settings classes, and each contract it fills, called live
  (`lint.registration`, `lint.conformance`, `lint.live`, `lint.contracts`), and a
  deprecation warning for settings that leave
  `untaped` to guess what needs setting up from the names `base_url` and
  `token` instead of a marked server and a declared credential
  (`lint.unmarked-service`);
- the rules that read its files: stderr wording, layering, structure, the
  import, terminal and blob-reader boundaries, databases opened through a store
  ([conventions](reference/conventions.md#enforcement)).

`untaped plugin list -f pipe | untaped lint --stdin` checks every installed
plugin. In a plugin's tests, `untaped_lint.testing.assert_clean("<name>")`
fails where `untaped lint` would exit non-zero (`strict=True` for warnings
too) and lists every finding, so CI catches what the command would.

`untaped.sdk.testing.check_conventions` is deprecated and goes at 12.0: it
gives the same rows as before, but runs the file rules only when
`untaped[lint]` is installed. The command that printed them is gone.

## Linting a repository

`untaped lint` also lints the files of the repository it runs in, against
every rule that reads them: the plugins' and the repository's own, which
live beside a file at the root, `.untaped/lint.yml`:

```yaml
version: 1
name: house                    # the key of this repository's own rules: house.no-print
exclude: ["vendor/**"]         # left out, on top of what git ignores
linters:
  house:
    rules: .untaped/lint       # a directory in the repository: each *.py directly in it
readers:
  awx: ["specs/**/*.yml"]      # a plugin reads these files into its own records
ignore:
  - house.no-print:tests/**
```

The file is found walking up from the current directory to the project
root (the nearest directory holding `.git`); `--config PATH` reads another
file and lints the current directory with its rules. Every key is spelled
like a plugin name and may not be `untaped` or an installed plugin's name.
The files linted are what git tracks plus untracked files git doesn't
ignore, so a new file is linted before `git add`; outside a checkout, every
file under the root. `PATHS` report only findings in files matching their
globs, as for a plugin.

A rule module is Python that `untaped lint` imports in its own process when
you lint that checkout, as `pytest` imports a `conftest.py`: no sandbox and
no confirmation. Review a repository's `.untaped/lint/` before you lint it,
as you would its tests. A module imports only the standard library,
`untaped_lint.api` and an installed plugin's `untaped_<plugin>.api`
(`lint.rule-imports` reports anything else), because it runs in untaped's
environment, not the repository's; importing a plugin that isn't installed
exits 4 naming the extra to install. A rule that needs a library belongs in
a plugin. A file a rule asks for that isn't UTF-8 is one
`lint.unreadable-file` finding; the rule still reads the rest.

```python
# .untaped/lint/no_print.py
from collections.abc import Iterator

from untaped_lint.api import FileLocation, Finding, Tree, finding, rule, target_of


@rule("no-print", reads=Tree, severity="error", summary="No print() in library code.")
def no_print(tree: Tree) -> Iterator[Finding]:
    for file in tree.files("src/**/*.py"):
        for number, line in enumerate(file.lines, start=1):
            if "print(" in line:
                yield finding(no_print, target_of(file), "print() in library code",
                              location=FileLocation(path=file.path, line=number))
```

`tree.files(*globs)` yields each matching file with its `path`, `text`,
`lines`, and `json()`, `yaml()` and `python()` (the parsed file).
`untaped lint new ID` writes such a module and its test: in
`tests/lint/` through `untaped_lint.testing` (`repo_rule`, `run_rule`,
`tree`) when the root has a pytest configuration, otherwise a `good` and a
`bad` fixture directory under the rules directory, which the run leaves out
of the tree, and the two commands that check them. It creates the lint file
and its `linters:` entry when missing.

## Rule ids

A rule's public id is `<key>.<id>`: the key is the plugin that declares it
(`lint` for lint's own) or the repository file's key (`house`), the id is
what the rule wrote (`error-case`). The
id alone is accepted where exactly one installed rule carries it.
`untaped lint rules` lists every rule installed, with what it reads, its
severity and its summary. A source line may carry
`# untaped: allow <rule>` (the bare id or `<key>.<id>`) where the rule
allows it; a bare id two keys declare allows nothing, with a warning.

## Leaving findings out

A suppression is `<rule>[:<target-glob>]`, the same everywhere it is
written:

- `lint.ignore` (a list) in `config.yml`, for every run (under
  `profiles.<name>`, for every run in that profile);
- `ignore:` in `.untaped/lint.yml`, for every run in the repository;
- `--ignore`, for one run (repeatable).

The lists add up. The target of a plugin's finding is the plugin's name; a
file's is its path. In
the glob `*` stops at `/` and `**` crosses it. `--select` reports only the
rules named; PATHS report only findings in files matching their globs.
Selection narrows what is reported, never what is read. It never drops
`lint.plugin-not-installed` or `lint.quarantined`, which say no rule ran,
or `lint.unreadable-file`, which says the convention rules didn't. An id no
installed rule declares is a warning; `untaped doctor` checks `lint.ignore`
(`lint.ignore-ids`).

## Exit codes

| Code | When |
|---|---|
| 0 | No finding, or warnings only |
| 3 | A finding of severity `error`, or any finding with `--strict` |
| 1 | A rule or provider could not run (an `untaped.failure` on stderr), or the lint file is unreadable or invalid |
| 2 | A usage mistake: a malformed `--select` or `--ignore`, `--stdin` with PATHS or with input that is not kinded `--format pipe` records, or a lint file key that is misspelled, `untaped` or an installed plugin's name |
| 4 | A configuration problem, such as every provider quarantined, a rule module importing a plugin that isn't installed, or a `readers:` key no installed plugin fills |

The most severe wins: a rule that could not run (1) is never hidden by an
error finding (3). Findings go to stdout as `lint.finding`
([records](reference/records.md#lint)); a summary such as
`lint: 3 errors, 1 warning` goes to stderr.

## Writing rules

A plugin with rules fills the `Linter` contract from `untaped_lint.api`.
Write each rule with `@rule` and host them in a `Rules` subclass:

```python
from collections.abc import Iterator

from untaped_lint.api import Finding, Plugin, Rules, finding, rule


@rule("readme", reads=Plugin, severity="warning", summary="A plugin ships a README.md.")
def readme(plugin: Plugin) -> Iterator[Finding]:
    if plugin.source_dir and not (plugin.source_dir.parent.parent / "README.md").is_file():
        yield finding(readme, plugin.name, "no README.md beside pyproject.toml")


class AcmeRules(Rules):
    RULES = (readme,)
```

A plugin lists `AcmeRules` in its plugin class's `fills`
and declares the dependency under an extra named `lint`, as for
[any owner](contracts.md). Its ids are then `acme.readme`. `Rules` validates
each subject once as the model a rule reads, runs each rule on its own, and
turns a rule that raises into a failure row (exit 1) while the other rules'
findings still count. A rule over `untaped.installed_plugin` sees the record, never
the composed root: composing inside a contract call fails.

A plugin that turns a repository's files into its own records fills
`TreeReader` (`read(tree) -> list[Subject]`): it runs where a `readers:`
entry names it, on the files its globs match, and the rules reading those
records run on what it returns. A file it can't read is carried in the
subject for its own rule to report, never raised. awx's reader takes apply
documents (`readers: awx: [...]`) and lints them as
`untaped awx lint FILES --offline` does.

A plugin with a lint command of its own (`untaped awx lint`) builds its
subjects and calls `untaped_lint.api.lint(subjects, config=lint_config(Path.cwd(),
tree=False), ...)`: `lint_config` reads the profile's `lint.ignore` and the
repository's lint file, so the plugin never reads lint's settings, and
`lint` prints the rows and exits by the table above. `only=` (target globs)
and `exact=` (literal targets) narrow what it reports by target;
`locations=` narrows by location, as the table writes it
(`job_templates/14`), where targets are not unique.
