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
  requirements, the help tree, stability marks, error and settings classes,
  the import boundary, and each contract it fills, called live
  (`lint.registration`, `lint.conformance`, `lint.live`, `lint.fills`,
  `lint.schema`, `lint.owner-schema-drift`, `lint.contracts`,
  `lint.migrations`);
- the rules that read its files: stderr wording, layering, structure, the
  terminal and blob-reader boundaries
  ([conventions](reference/conventions.md#enforcement)).

`untaped plugin list -f pipe | untaped lint --stdin` checks every installed
plugin. In a plugin's tests, `untaped_lint.testing.assert_clean("<name>")`
fails where `untaped lint` would exit non-zero (`strict=True` for warnings
too) and lists every finding, so CI catches what the command would.

`untaped plugin check` and `untaped.testing.check_conventions` are
deprecated and go at 12.0: they print the same rows as before, but run the
file rules only when `untaped[lint]` is installed.

## Rule ids

A rule's public id is `<key>.<id>`: the key is the plugin that declares it
(`lint` for lint's own), the id is what the plugin wrote (`error-case`). The
id alone is accepted where exactly one installed rule carries it.
`untaped lint rules` lists every rule installed, with what it reads, its
severity and its summary. A source line may carry
`# untaped: allow <rule>` (the bare id or `lint.<rule>`) where the rule
allows it.

## Leaving findings out

A suppression is `<rule>[:<target-glob>]`, the same everywhere it is
written:

- `profiles.<name>.lint.ignore` (a list), for every run in the profile;
- `--ignore`, for one run (repeatable).

The lists add up. The target of a plugin's finding is the plugin's name. In
the glob `*` stops at `/` and `**` crosses it. `--select` reports only the
rules named; PATHS report only findings in files matching their globs.
Selection narrows what is reported, never what is read. It never drops
`lint.plugin-not-installed` or `lint.quarantined`, which say no rule ran,
or `lint.unreadable-file`, which says the file rules didn't. An id no
installed rule declares is a warning; `untaped doctor` checks `lint.ignore`
(`lint.ignore-ids`).

## Exit codes

| Code | When |
|---|---|
| 0 | No finding, or warnings only |
| 3 | A finding of severity `error`, or any finding with `--strict` |
| 1 | A rule or provider could not run (an `untaped.failure` on stderr) |
| 2 | A usage mistake: a malformed `--select` or `--ignore`, `--stdin` with PATHS |
| 4 | A configuration problem, such as every provider quarantined |

The most severe wins: a rule that could not run (1) is never hidden by an
error finding (3). Findings go to stdout as `lint.finding`
([records](reference/records.md#lint)); a summary such as
`lint: 3 errors, 1 warning` goes to stderr.

## Writing rules

A plugin with rules fills the `Linter` contract from `untaped_lint.api`.
Write each rule with `@rule` and host them in a `Rules` subclass:

```python
from collections.abc import Iterator

from untaped.sdk import Plugin
from untaped_lint.api import Finding, Rules, finding, rule


@rule("readme", reads=Plugin, severity="warning", summary="A plugin ships a README.md.")
def readme(plugin: Plugin) -> Iterator[Finding]:
    if plugin.source_dir and not (plugin.source_dir.parent.parent / "README.md").is_file():
        yield finding(readme, plugin.name, "no README.md beside pyproject.toml")


class AcmeRules(Rules):
    RULES = (readme,)
```

Offer it with `provides={"lint": lambda: (AcmeRules(),)}` and declare the
dependency under an extra named `lint`, as for
[any owner](contracts.md). Its ids are then `acme.readme`. `Rules` validates
each subject once as the model a rule reads, runs each rule on its own, and
turns a rule that raises into a failure row (exit 1) while the other rules'
findings still count. A rule over `untaped.plugin` sees the record, never
the composed root: composing inside a contract call fails.
