# untaped-awx

Install it as part of `untaped`: `uv tool install 'untaped[awx]'` or `pip install 'untaped[awx]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped awx` reads and changes Ansible Automation Platform (AAP) or AWX
resources by name, launches and follows jobs, and tests playbook changes with
declarative suites. It is for people and agents who manage a shared
controller from the command line or from CI. `awx test` is
[experimental](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/versioning.md#experimental)
and may change in a minor release.

## Set up

Point a profile at the controller, give it a token, then check the
connection:

```bash
untaped config set awx.base_url https://aap.example.com
untaped auth set awx
untaped config set awx.api_prefix /api/v2/   # standalone AWX only
untaped awx ping
```

`ping` names the authenticated `user`. `auth set` stores the token with your
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
selection in your editor as one multi-document file. Writes preview and ask first; see the skill.

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

The [packaged skill](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-awx/src/untaped_awx/skills/untaped-awx/SKILL.md) is the full reference: every workflow, safety rule and pitfall. Install it for your agent with `untaped skills install awx --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)); `untaped awx COMMAND --help` lists each command's options.

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#awx) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#awx)
