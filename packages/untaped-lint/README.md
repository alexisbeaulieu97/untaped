# untaped-lint

Install it as part of `untaped`: `uv tool install 'untaped[lint]'` or `pip install 'untaped[lint]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped lint` runs rules over plugins, repositories and records and reports
findings, each with a rule id (`lint.error-case`, `house.no-print`), a
severity and what it is about. Every plugin's rules, and a repository's own
in `.untaped/lint/`, share the one id, ignore and exit spelling. It is
experimental.

## Use

In a plugin repository, `untaped lint` checks each plugin the
`pyproject.toml` declares: the house conventions, the contract fills, and the
rules that read the plugin's files. Install the plugin in the same
environment first (`uv pip install -e .`).

Elsewhere it runs the repository's own conventions: rule modules in
`.untaped/lint/`, named by `.untaped/lint.yml`, which `untaped lint` imports
the way pytest imports a `conftest.py` (read them before you lint a
checkout you don't know). `untaped lint new no-print --key house` starts
one and its test.

```bash
untaped lint                              # this repository: its plugins and its files
untaped lint --select lint.error-case     # one rule
untaped lint --ignore 'lint.print:acme'   # leave a rule out for one run
untaped lint rules                        # every rule, the repository's own included
untaped lint new no-print                 # a new repository rule, test first
untaped plugin list -f pipe | untaped lint --stdin   # every installed plugin
```

It exits 3 when a rule reports an error, 1 when a rule could not run, and 0
otherwise. `lint.ignore` in a profile lists findings every run leaves out.
In a plugin's tests, `untaped_lint.testing.assert_clean("<name>")` fails with
every finding.

[docs/lint.md](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/lint.md)
explains ids, ignores, exit codes and writing rules. The
[packaged skill](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-lint/src/untaped_lint/skills/untaped-lint/SKILL.md)
is the agent's reference; `untaped lint --help` lists the options.
