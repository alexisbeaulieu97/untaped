---
name: untaped-awx
description: Use the built-in `untaped awx` capability for AWX/AAP workflows.
---

# Untaped AWX/AAP

Use this skill when the user wants an agent to operate the `untaped awx` CLI for Ansible Automation Platform or AWX resources.

## Setup

- The command is `untaped awx`. It ships with the unified `untaped` CLI (no separate install).
- Settings live under `profiles.<name>.awx`: `base_url`, `token`, `api_prefix`, `default_organization`, and `page_size`.
- AAP uses the default `awx.api_prefix` of `/api/controller/v2/`; upstream AWX users usually set `/api/v2/`.
- Use `untaped config set awx.token --prompt` or `--stdin` for tokens.
- Run `untaped awx ping` before a workflow when the profile or controller may be stale; it also checks the token via `/me/` and reports the authenticated `user`.

## Resource and selection patterns

- The eight writable groups are `job-templates`, `workflow-templates`, `projects`, `schedules`, `hosts`, `groups`, `inventories`, and `inventory-sources`. Credentials, credential types, organizations, unified templates, and job records remain read-only or action-specific views.
- Selection modes are exclusive: positional names, names with `--by-id`, `--stdin`, `--filter`/`--search`, or `--all`. Organization, inventory, inventory-organization, and parent scopes constrain lookup and filters.
- Prefer typed pipes for composition. `--format pipe` carries kind and ID, and a `--stdin` consumer uses those IDs directly:

  ```bash
  untaped awx job-templates list --filter name__icontains=deploy --format pipe \
    | untaped awx job-templates patch --stdin --set verbosity=2
  ```

- Mutation selection is complete before any write. Empty or invalid selections do not partially mutate a batch. Machine data is stdout; previews, prompts, and progress are stderr.

## Patch and edit

- Use `patch` for the same field on existing resources:

  ```bash
  untaped awx inventory-sources patch \
    --filter inventory__name=Production \
    --set update_cache_timeout=3600
  ```

- `--set` is repeatable and JSON-coerced, except that a field the record holds as a string stays a string unless the value is a JSON object/array (`scm_branch=1.10` stays `"1.10"`). Unknown field names that closely match a known field are rejected with a "did you mean" hint unless `--allow-unknown-fields`; other unknown names are sent with a warning; `--patch-file` accepts a YAML/JSON mapping, with `--set` taking precedence. Values replace top-level fields; omitted fields remain unchanged and nested maps are not merged. Foreign-key integers are IDs; strings are names in scope. To target a numeric-looking name, preserve the JSON string: `--set 'inventory="123"'`; unquoted `inventory=123` is ID 123.
- Inventory cache timeouts are seconds, and `0` is valid. Changing the timeout does not toggle `update_on_launch`. Maps replace exactly, lists preserve order, and known secrets are redacted.
- Patch and edit cannot create, rename, reparent, retarget, or change identity. Use `apply` for create/update and `delete` for removal.
- `edit` opens one YAML multi-document batch. `--field` limits editable fields; missing fields stay unchanged and removing a document deselects it. Set `VISUAL`/`EDITOR` to a waiting editor such as `code --wait`.
- Edit requires a real `/dev/tty`, even with piped stdin or `--yes`. Editor streams use that terminal. A failed session keeps the YAML file and prints its path. Invalid YAML can be reopened or cancelled, and a no-op does not prompt or write.

## Apply, export, sync, and execution tracking

- `untaped awx apply FILE_OR_DIRECTORY` is the declarative complete-document create/update path. `export` writes a fixed selection as portable YAML; `$encrypted$` placeholders preserve controller secrets. Workflow template exports do not round-trip node graphs.
- Inventory and source settings preserve organization and parent identity. Operation support is specific: inventory sync rejects smart/source-less inventories and invalid or manual sources during preflight, while apply accepts representable inventory documents and rejects only incompatible source/configuration combinations. Inventory settings changes do not rewrite source-managed hosts or groups.
- `launch --extra-vars` is repeatable: `KEY=VAL` (only true/false/null, integers, and JSON objects/arrays decoded; `1.10` stays a string), `@FILE` (YAML/JSON mapping), or a raw JSON/YAML mapping; entries merge into one JSON mapping; YAML dates become ISO strings. Launch checks the template's launch settings first: a flag whose `ask_*_on_launch` is false (unless its value equals the template's own), an extra var outside the survey when only the survey prompts, or a missing required survey variable, is a usage error before any POST; a response with `ignored_fields` fails that row. `launch --host-pattern` limits the hosts. `jobs list` defaults to the newest 20 (`--limit 0` for all); `--template NAME|ID` (digits mean an id) keeps one template's runs. `get` defaults to a table; use `-f yaml`/`-f json` for full records. Ctrl-C during submission or `--wait`/`--track` exits 130 and prints the executions not known to have finished with a `jobs wait` hint.
- Use `projects sync`, `inventory-sources sync`, and `inventories sync`. `--wait` fails on unsuccessful terminal states; `--track` writes progress to stderr, with the failure reason under each failed or unreachable host. `launch`/`sync --timeout SECONDS` (with `--wait`/`--track`) stops waiting: a still-running execution fails its row, keeps running, and is named in a `jobs wait` hint. Known invalid sync selections produce zero POSTs.
- `jobs cancel ID...` and `jobs relaunch ID... [--failed-hosts]` read every target first, preview, and ask once (`--yes`, `--dry-run`). Cancel rows are `awx.cancel_outcome` (`cancel_requested`, or `skipped` when already finished); relaunch rows are `awx.relaunch_outcome` whose `id`/`kind` name the new execution, so they pipe into `jobs wait --stdin`. `--failed-hosts` applies to job executions only; project and inventory updates cannot be relaunched.
- `jobs events`/`jobs logs` with several ids print one json/yaml array (each row has `job`); `--follow --format json` streams NDJSON instead.
- Ordinary jobs expose `job_events`; project and inventory updates expose `events`. Workflow jobs, including sliced launch results, have no events or stdout route: `--track` polls status instead. Use `--kind project_update` or `--kind inventory_update` for non-default `jobs` commands; use `jobs wait` for workflow jobs, not workflow `events` or `logs`.
- Writes are serial by default, `--parallel` is capped at ten, and runtime failure stops new scheduling unless `--continue-on-error` is supplied. Already-running requests finish; partial results retain IDs. There is no transaction or rollback. Async inventory deletion reports `deletion_requested`.

## Confirmations and output

- `patch`, `edit`, `apply`, and `delete` show one redacted preview and default-No confirmation. `--yes` skips it; `--dry-run` never writes and wins over `--yes`. Declining exits 1 (`cancelled; no changes made`). Configuration writes without a controlling terminal require `--yes` or `--dry-run` (exit 2). A piped record of another kind exits 2; empty `--stdin` is an error. A single named `launch`/`sync` submits immediately; multiple targets or an `--all`/`--filter`/`--search`/`--stdin` selection lists the targets and asks once (`--yes` skips, `--dry-run` previews).
- Keep stdout data-only and prefer `--format json`, `yaml`, or `pipe` for automation. `list` default columns apply to `table`/`raw` only. Launch/sync rows are `awx.launch_outcome`/`awx.sync_outcome` (pipe them into `jobs wait --stdin`); previews use the action `planned`; `fields_changed` and `preserved_secrets` are lists. Never expose secrets; preserve `$encrypted$` placeholders.

For the full user guide, see `docs/awx/usage.md` in the untaped repository.
