# AWX/AAP usage

Use `untaped awx ...` for Ansible Automation Platform (AAP) or AWX. The
current command surface is generated from the resource catalog; use
`untaped awx --help` and `untaped awx <resource> --help` for the complete list
of fields and options.

## Connect to AAP

The default AAP gateway prefix is `/api/controller/v2/`. Standalone AWX
usually uses `/api/v2/`. Configure the profile, then check the connection:

```bash
untaped config set awx.base_url https://aap.example.com
printf '%s\n' "$AAP_TOKEN" | untaped config set awx.token --stdin
untaped awx ping
```

`ping` reads the unauthenticated `/ping/` health endpoint and then `/me/`, so
a rejected token fails the command; the output includes the authenticated
`user`.

Use `untaped --profile <name> awx ...` to select a different configured
profile. Tokens are secret settings; do not put them in a manifest or command
history. To keep the token out of `config.yml` too, set `awx.token_command`
to a command that prints it, for example
`untaped config set awx.token_command '["pass", "show", "aap/token"]'`, or
export `CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN` or `AAP_TOKEN` (the
variables the `ansible.controller` collection reads). `awx.token` wins over
`awx.token_command`, which wins over the variables, tried in that order; see
[Tokens](../configuration.md#tokens).

## Where the details are

The complete reference ships with the CLI in the `untaped-awx` skill, which
an agent reads too; install it with `untaped skills install awx`. Each task
below links to the skill page that has the details.

## Find and read resources

```bash
untaped awx job-templates list --filter name__icontains=deploy
untaped awx inventory-sources list --inventory Production --inventory-organization Default
untaped awx job-templates get Deploy --organization Default --format yaml
```

Select by name (scoped by `--organization`, `--inventory`, `--parent`), with
`--by-id`, `--filter`/`--search`, `--stdin` or `--all`. Read `json`, `yaml`
or `pipe` output rather than a table, and chain commands with
`--format pipe | … --stdin`. See
[selecting resources](../../src/untaped/capabilities/awx/skills/untaped-awx/references/resources.md#select-resources).

## Change fields

```bash
untaped awx inventory-sources patch Cloud --inventory Production \
  --inventory-organization Default --set update_cache_timeout=3600
untaped awx job-templates edit --filter name__icontains=deploy --field verbosity
```

`patch` sets the same values on every selected resource; `edit` opens them in
`$VISUAL`/`$EDITOR` to change each one differently. Neither creates nor
renames: `apply` creates, and `job-templates`/`workflow-templates` have
`copy` and `rename`. See
[changing resources](../../src/untaped/capabilities/awx/skills/untaped-awx/references/resources.md#patch-fields) for value coercion,
name-vs-id references, secrets, the editor session, copy and rename.

## Export and apply documents

```bash
untaped awx job-templates export Deploy --organization Default --out deploy.yml
untaped awx apply ./awx-specs --dry-run
untaped --profile staging awx export --kind job-templates --out-dir exported \
  | untaped --profile prod awx apply - --yes
```

`apply` creates or updates every kind at once from YAML documents, after one
preview; `--check` exits 3 on drift and `--source-ref REF` reads the files at
a git ref. The document format, what an export cannot carry (secrets,
access, history) and workflow node graphs are in
[resource documents](../../src/untaped/capabilities/awx/skills/untaped-awx/references/specs.md).

## Launch, sync and follow jobs

```bash
untaped awx job-templates launch Deploy --organization Default \
  --extra-vars @vars.yml --extra-vars version=1.10.0 --host-pattern web --follow
untaped awx projects sync Playbooks --wait
untaped awx inventories sync Production --follow
```

`--wait` and `--follow` wait for the result (`--follow` streams the log to
stderr), `--timeout` bounds the wait and `--cancel` cancels what the command
stops watching. A launch field the template does not prompt for is refused
before anything runs. `awx jobs` lists, inspects, cancels and relaunches
executions:

```bash
untaped awx jobs list --status failed --limit 10
untaped awx jobs logs 101 --tail 50 --follow
untaped awx jobs relaunch 101 --failed-hosts --yes --format pipe \
  | untaped awx jobs wait --stdin
```

See [jobs](../../src/untaped/capabilities/awx/skills/untaped-awx/references/jobs.md).

## Memberships, SCM source and usage

```bash
untaped awx job-templates credentials add Deploy "Vault prod" --organization Default
untaped awx groups hosts add web web-01 web-02 --inventory Production
untaped awx job-templates list --organization Default --with-scm \
  --columns name,scm_url,effective_scm_ref
untaped awx job-templates usage Deploy --recursive
```

See [memberships](../../src/untaped/capabilities/awx/skills/untaped-awx/references/resources.md#memberships) and the sections after it.

## Test suites

`awx test` is [experimental](../stability.md#experimental) and may change in
a minor release. It launches a template with a matrix of cases from a YAML
suite under `.untaped/awx/tests/` and checks each job against what the case
expects:

```bash
untaped awx test init "Deploy app"
untaped awx test validate
untaped awx test run --scm-branch HEAD --format json
untaped awx test run --scm-branch HEAD --baseline main
```

See the [suite format](../../src/untaped/capabilities/awx/skills/untaped-awx/references/test-suites.md),
[reading results](../../src/untaped/capabilities/awx/skills/untaped-awx/references/test-results.md) (what to do about each failure) and the
[example suites](../../src/untaped/capabilities/awx/skills/untaped-awx/examples/).
To let an AI agent run suites against its own changes, give it a dedicated
AWX user, token and profile: see [AWX agent profile](../../src/untaped/capabilities/awx/skills/untaped-awx/references/agent-profile.md).

## Confirmations and failures

Writes (`patch`, `edit`, `apply`, `delete`, `copy`, `rename`, membership
`add`/`remove`, `jobs cancel`, `jobs relaunch`) preview once and ask with No as
the default; `--dry-run` never writes and `--yes` skips the prompt, which a
write without a terminal needs. Launching or syncing several targets asks
once too. See
[confirmations and batches](../../src/untaped/capabilities/awx/skills/untaped-awx/references/resources.md#confirmations-and-batches), and
[Exit codes](../reference/exit-codes.md) for what each exit code means.

## Optional disposable live-AAP smoke

To try the write path safely, pick a disposable inventory and a harmless
source on a configured controller, run a smoke test like this, and restore the
exported files afterward:

```bash
untaped awx ping
untaped awx inventories export Disposable --organization Default \
  --out disposable-inventory.yml
untaped awx inventory-sources export DisposableSource --inventory Disposable \
  --inventory-organization Default --out disposable-source.yml

untaped awx inventory-sources patch DisposableSource \
  --inventory Disposable --inventory-organization Default \
  --set update_cache_timeout=0 --dry-run
untaped awx inventory-sources patch DisposableSource \
  --inventory Disposable --inventory-organization Default \
  --set update_cache_timeout=0 --yes
untaped awx inventory-sources get DisposableSource \
  --inventory Disposable --inventory-organization Default --format yaml

untaped awx inventory-sources edit DisposableSource \
  --inventory Disposable --inventory-organization Default --dry-run
untaped awx inventory-sources sync DisposableSource \
  --inventory Disposable --inventory-organization Default --follow

untaped awx apply disposable-source.yml --yes
untaped awx apply disposable-inventory.yml --yes
```

Confirm that the cache timeout changed, `update_on_launch` stayed unchanged,
the no-op editor made no write, and the sync reached the expected terminal
state. These steps are opt-in live writes against a disposable controller.

## See also

- [Getting started](../getting-started.md)
- [Pipes and record kinds](../reference/pipes.md)
- [Configuration reference](../reference/config.md#awx)
- [Exit codes](../reference/exit-codes.md)
