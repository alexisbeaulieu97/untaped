# untaped-awx

Install it as part of `untaped`: `uv tool install 'untaped[awx]'` or `pip install 'untaped[awx]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped awx ...` reads and changes Ansible Automation Platform (AAP) or AWX
resources by name, launches and follows jobs, and tests playbook changes with
declarative suites. The command surface is generated from the resource
catalog: `untaped awx --help` and `untaped awx <resource> --help` list every
command and option. [`--columns '?'`](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#output-records) lists a command's columns after it runs,
so use it on a read command (or add `--dry-run` to a write).

The detailed manual ships with the CLI as the `untaped-awx` skill
(`untaped skills install awx`). This page covers concepts, workflows that
span commands, and surprising behavior, and links to the skill for detail.

## Connect to AAP

The default AAP gateway prefix is `/api/controller/v2/`. Standalone AWX
usually uses `/api/v2/`. Configure the profile, then check the connection:

```bash
untaped config set awx.base_url https://aap.example.com
printf '%s\n' "$AAP_TOKEN" | untaped config set awx.token --stdin
untaped awx ping
```

`ping` reads the unauthenticated `/ping/` health endpoint and then `/me/`, so
a rejected token fails the command; the output names the authenticated
`user`.

Use `untaped --profile <name> awx ...` to select another profile. Keep tokens
out of manifests and command history. To keep one out of `config.yml` too,
set `awx.token_command` to a command that prints it
(`untaped config set awx.token_command '["pass", "show", "aap/token"]'`), or
export one of the variables the `ansible.controller` collection reads; see
[Tokens](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/configuration.md#tokens) for the precedence.

## Concepts

- **Selection.** Every command picks its targets by name (scoped by
  `--organization`, `--inventory` or `--parent`), by id with `--by-id`, by
  query with `--filter`/`--search`, from `--stdin`, or with `--all`. Names
  resolve inside a scope, so a name used in two organizations is ambiguous
  until you pass `--organization` or set `awx.default_organization`. The
  whole selection resolves before the first write.
- **Typed pipes.** `--format pipe` emits records that carry their kind and
  id; a `--stdin` consumer of the same kind uses the ids directly, so a
  `list` feeds a `patch` or a `launch` feeds `jobs wait` without re-resolving
  names. See [Scripting](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#awx).
- **Documents.** `export` writes resources as portable YAML documents that
  reference other resources by name, and `apply` creates or updates every
  kind from them in dependency order. They are how you create resources, copy
  configuration between controllers, and keep it in git.
- **The controller runs what the remote holds.** A job checks out the
  project's remote branch, so a local commit proves nothing until it is
  pushed; `--scm-branch HEAD` and `--source-ref HEAD` refuse an unpushed
  HEAD for that reason.

## Find and change resources

```bash
untaped awx job-templates list --filter name__icontains=deploy
untaped awx inventory-sources list --inventory Production --inventory-organization Default
untaped awx inventory-sources patch Cloud --inventory Production \
  --inventory-organization Default --set update_cache_timeout=3600
untaped awx job-templates edit --filter name__icontains=deploy --field verbosity
```

`patch` sets the same values on every selected resource; `edit` opens them in
`$VISUAL`/`$EDITOR` to change each one differently. Neither creates nor
renames: `apply` creates, and templates have `copy` and `rename`. A value
replaces the whole top-level field (nested maps are not merged), and a field
AWX stores as a string stays a string (`scm_branch=1.10` is `"1.10"`).
Memberships (credentials, labels, hosts in groups) have their own idempotent
`add`/`remove` commands. See
[changing resources](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-awx/src/untaped_awx/skills/untaped-awx/references/resources.md).

## Export and apply documents

```bash
untaped awx job-templates export Deploy --organization Default --out deploy.yml
untaped awx apply ./awx-specs --dry-run
untaped --profile staging awx export --kind job-templates --out-dir exported \
  | untaped --profile prod awx apply - --yes
```

`apply --check` exits 3 on drift, which makes it a CI gate for configuration
kept in git; `--source-ref REF` applies the files as they are at a git ref.
A document cannot carry secrets, access or history: password survey defaults
and callback keys (`host_config_key`) export as `$encrypted$`, which keeps the
stored value when applied back to the same resource. See
[resource documents](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-awx/src/untaped_awx/skills/untaped-awx/references/specs.md).

## Launch, sync and follow jobs

```bash
untaped awx job-templates launch Deploy --organization Default \
  --extra-vars @vars.yml --extra-vars version=1.10.0 --host-pattern web --follow
untaped awx jobs relaunch 101 --failed-hosts --yes --format pipe \
  | untaped awx jobs wait --stdin
```

A launch field the template does not prompt for (`ask_*_on_launch` false) is
refused before anything runs, because AWX would silently ignore it. A single
named launch or sync submits at once; several targets are listed and
confirmed once. A job the command stops watching keeps running unless you
pass `--cancel`. See
[jobs](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-awx/src/untaped_awx/skills/untaped-awx/references/jobs.md).

## Test suites

`awx test` is [experimental](https://github.com/alexisbeaulieu97/untaped/blob/main/README.md#experimental) and may change in
a minor release. A suite under `.untaped/awx/tests/` in the playbook
repository launches a template once per case and checks each job against what
the case expects. The loop is: write a suite (`test init`), save a baseline
of the base branch, push, `test validate`, then `test run --scm-branch HEAD
--compare` the baseline:

```bash
untaped awx test init "Deploy app"
untaped awx test run --scm-branch main --format json > /tmp/baseline.json
untaped awx test validate
untaped awx test run --scm-branch HEAD --compare /tmp/baseline.json --format json
```

A failing row names the system that must act (the playbook, SCM, inventory,
controller, hosts or the suite), so a failure caused by the environment is
not blamed on the change. When the change also edits template specs under
`.untaped/awx/`, `--source-ref HEAD` runs the suites against temporary copies
of those specs.

See the [suite format](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-awx/src/untaped_awx/skills/untaped-awx/references/test-suites.md),
[reading results](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-awx/src/untaped_awx/skills/untaped-awx/references/test-results.md)
and the [example suites](https://github.com/alexisbeaulieu97/untaped/tree/main/packages/untaped-awx/src/untaped_awx/skills/untaped-awx/examples/).
To let an AI agent run suites against its own changes, give it a dedicated
AWX user, token and profile: see [AWX agent profile](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-awx/src/untaped_awx/skills/untaped-awx/references/agent-profile.md).

## Confirmations and failures

Writes (`patch`, `edit`, `apply`, `delete`, `copy`, `rename`, membership
`add`/`remove`, `jobs cancel`, `jobs relaunch`, `test prune`) preview once and
ask with No as the default (see
[Commands that change things](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#commands-that-change-things)). There is no
rollback: a batch that fails partway keeps what it wrote, so export before a
large change to have something to apply back. `edit` needs a real terminal
even with `--yes`. See
[confirmations and batches](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-awx/src/untaped_awx/skills/untaped-awx/references/resources.md#confirmations-and-batches)
and [Exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#exit-codes).

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

- [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md)
- [Scripting](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#awx)
- [Configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#awx)
- [Exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#exit-codes)
