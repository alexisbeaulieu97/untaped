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
- Run `untaped awx ping` before a workflow when the profile or controller may be stale.

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

- `--set` is repeatable and JSON-coerced; `--patch-file` accepts a YAML/JSON mapping, with `--set` taking precedence. Values replace top-level fields; omitted fields remain unchanged and nested maps are not merged. Foreign-key integers are IDs; strings are names in scope. To target a numeric-looking name, preserve the JSON string: `--set 'inventory="123"'`; unquoted `inventory=123` is ID 123.
- Inventory cache timeouts are seconds, and `0` is valid. Changing the timeout does not toggle `update_on_launch`. Maps replace exactly, lists preserve order, and known secrets are redacted.
- Patch and edit cannot create, rename, reparent, retarget, or change identity. Use `apply` for create/update and `delete` for removal.
- `edit` opens one YAML multi-document batch. `--field` limits editable fields; missing fields stay unchanged and removing a document deselects it. Set `VISUAL`/`EDITOR` to a waiting editor such as `code --wait`.
- Edit requires a real `/dev/tty`, even with piped stdin or `--yes`. Editor streams use that terminal. The session directory is `0700` and the YAML is `0600`; clean sessions remove them, while failures retain the file and print its path. Invalid YAML can be reopened or cancelled, and a no-op does not prompt or write.

## Apply, save, sync, and execution tracking

- `untaped awx apply FILE_OR_DIRECTORY` is the declarative complete-document create/update path. `save` exports a fixed selection as portable YAML; `$encrypted$` placeholders preserve controller secrets. Workflow template exports do not round-trip node graphs.
- Inventory and source settings preserve organization and parent identity. Operation support is specific: inventory sync rejects smart/source-less inventories and invalid or manual sources during preflight, while apply accepts representable inventory documents and rejects only incompatible source/configuration combinations. Inventory settings changes do not rewrite source-managed hosts or groups.
- Use `projects sync`, `inventory-sources sync`, and `inventories sync`. Inventory sync freezes source IDs before submitting updates. `--wait` fails on unsuccessful terminal states; `--track` writes progress to stderr. Known invalid sync selections produce zero POSTs.
- Ordinary jobs expose `job_events`; project and inventory updates expose `events`. Workflow jobs, including sliced launch results, have no events or stdout route: `--track` polls status instead. Use `--kind project_update` or `--kind inventory_update` for non-default `jobs` commands; use `jobs wait` for workflow jobs, not workflow `events` or `logs`.
- Writes are serial by default, `--parallel` is capped at ten, and runtime failure stops new scheduling unless `--continue-on-error` is supplied. Already-running requests finish; partial results retain IDs. There is no transaction or rollback. Async inventory deletion reports `deletion_requested`.

## Confirmations and removed interfaces

- `patch`, `edit`, `apply`, and `delete` show one redacted preview and default-No confirmation. `--yes` skips it; `--dry-run` never writes and is mutually exclusive with `--yes`. Configuration writes without a controlling terminal require `--yes` or `--dry-run`. Launch and sync are explicit actions and do not add an edit confirmation.
- The former stdin apply overlay, project update verb, and fail-fast flag are unavailable. Use `patch --stdin --set`, `projects sync`, and `--continue-on-error`.
- Keep stdout data-only and prefer `--format json`, `yaml`, or `pipe` for automation. Never expose secrets; preserve `$encrypted$` placeholders.

For the full user guide and an opt-in disposable live-AAP smoke procedure, see
`docs/awx/usage.md` in the source repository. Development used strict HTTP
fakes and did not validate against a live AAP controller.
