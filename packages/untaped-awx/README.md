# untaped-awx

Install it as part of `untaped`: `uv tool install 'untaped[awx]'` or `pip install 'untaped[awx]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped awx` reads and changes Ansible Automation Platform (AAP) or AWX
resources by name, lints them, launches and follows jobs, and tests playbook
changes with declarative suites. It is for people and agents who manage a
shared controller from the command line or from CI. `awx lint` and `awx test`
are
[experimental](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/versioning.md#experimental)
and may change in a minor release.

## Set up

Point a profile at the controller, give it a token, then check the
connection:

```bash
untaped config set awx.server.url https://aap.example.com
untaped auth login awx
untaped config set awx.api_prefix /api/v2/   # standalone AWX only
untaped awx ping
```

`ping` names the authenticated `user`. `auth login` stores the token with your
password store, not in `config.yml`; an environment variable also works. See
[Tokens](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/configuration.md#tokens)
and the
[settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#awx).

## Find and change resources

```bash
untaped awx job-templates list --filter name__icontains=deploy
untaped awx job-templates get Deploy --organization Default --format yaml
untaped awx job-templates patch Deploy --organization Default --set verbosity=2 --dry-run
untaped awx job-templates edit --filter name__icontains=deploy --field verbosity
```

Targets are picked by name inside a scope, by id, by query or from a pipe.
`patch` sets the same values on every selected resource; `edit` opens the
selection in your editor as one multi-document file. Writes preview and ask first
(`untaped agents awx writes`).

## Export and apply documents

```bash
untaped awx job-templates export Deploy --organization Default --out deploy.yml
untaped awx apply ./awx-specs --dry-run
untaped --profile staging awx export --kind job-templates --out-dir exported \
  | untaped --profile prod awx apply - --yes
```

Exports are portable YAML that reference other resources by name. `apply`
creates or updates them in dependency order, so you can keep configuration
in git, copy it between controllers, and gate CI on `apply --check`.

## Lint resources

```bash
untaped awx lint ./awx-specs --offline     # the documents alone
untaped awx lint --organization Platform   # what the controller holds
```

On a template naming a project the files lack, and a schedule setting a
limit its template ignores, the first reports two rows (`--format json` for
every field) and ends with the summary `lint: 2 errors, 0 warnings`, exit 3.
The rows, trimmed here to their location, rule and message (the table also
shows each one's severity and target):

```text
awx-specs/templates.yml#2  awx.missing-reference     project 'web' is not in the batch
awx-specs/templates.yml#4  awx.ignored-launch-field  limit is set, but JobTemplate 'deploy-web' ignores it: ask_limit_on_launch is false
```

With files, `lint` checks apply documents before you apply them, resolving
references in the batch (`--offline`) or on the controller too; without, it
checks what the controller holds, from a snapshot kept for an hour. Findings
share `untaped lint`'s ids, `--ignore` and exit codes (3 on an error), and a
repository can lint its documents offline with `untaped lint`.
`untaped agents awx linting` lists the rules.

## Launch and follow jobs

```bash
untaped awx job-templates launch Deploy --organization Default \
  --extra-vars @vars.yml --host-pattern web --follow
untaped awx jobs relaunch 101 --failed-hosts --yes --format pipe \
  | untaped awx jobs wait --stdin
untaped awx jobs logs 101 --tail 50
```

You get the job's outcome when it finishes, and its log or events when you
need to diagnose it.

## Test a playbook change

```bash
untaped awx test init "Deploy app"
untaped awx test run --scm-branch main --format json > /tmp/baseline.json
# commit and git push the change, then:
untaped awx test validate --scm-branch HEAD
untaped awx test run --scm-branch HEAD --compare /tmp/baseline.json --format json
```

A suite in the playbook repository launches a template once per case and
checks each job against what the case expects. Comparing against the base
branch shows regressions, and each failing row names the system that must
act, so an environment failure is not blamed on your change.

## Reference

`untaped agents awx` lists every command with its options, the settings, and the guides on selecting and changing resources, export and apply documents, workflow node graphs, launching jobs, linting, writing and reading test suites, test runs at a git ref, and the AWX user an agent works as; the untaped you run generates it.

`untaped skills install awx --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)) points your agent at it.

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#awx) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#awx)
