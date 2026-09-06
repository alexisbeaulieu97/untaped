---
name: untaped-awx
description: Use the untaped-awx CLI.
---

# Untaped AWX/AAP

Use this skill when the user wants an agent to operate the `untaped awx` CLI for Ansible Automation Platform or AWX resources.

## Setup

- The command is `untaped awx`. It ships with the unified `untaped` CLI (no separate install).
- Settings live under `profiles.<name>.awx`: `base_url`, `token`, `api_prefix`, `default_organization`, and `page_size`.
- AAP uses the default `awx.api_prefix` of `/api/controller/v2/`; upstream AWX users usually set `/api/v2/`.
- Use `untaped config set awx.token --prompt` or `--stdin` for tokens.

## Command Patterns

- Use `untaped awx ping` before deeper workflows when credentials or base URL may be stale.
- Resource commands are spec-driven. Common resource groups include job templates, workflows, projects, credentials, inventories, hosts, groups, schedules, and execution records.
- Prefer `list --format raw --columns name` when selecting a resource for a follow-up command.
- Use `--format pipe` to chain commands richly: it emits one self-describing record per line tagged with a `kind` (e.g. `awx.job_template`, `awx.job`), and any `--stdin` consumer reads that stream back (e.g. `job-templates list --format pipe | job-templates get --stdin`).
- Use `get --format yaml` or `save` when the next step is editing an AWX object declaratively.
- Apply workflows preview by default; writes require `--yes` / `-y`. Fields you provide are passed through to AWX as-is (so version-specific fields work without a tool update); identity, read-only, and membership fields are handled automatically, and an unrecognized field is sent with a `warning:` rather than rejected.
- Apply verifies written body fields after POST/PATCH, checking the write response first and then one fallback GET. Unreflected fields fail the item by default; use `--allow-unverified` with `--yes` / `-y` only when an accepted-but-unproven write should remain best-effort.
- JobTemplate credentials are membership state, not PATCH-body state. Use `job-templates apply` with `credentials: [...]` for reconciliation or `job-templates credentials add/remove` for additive changes.
- `delete` is a guarded batch verb: it previews the targets on stderr and confirms before deleting. On a TTY it prompts; reading targets from a pipe (e.g. `inventories list --filter name__contains=test --format pipe | inventories delete --stdin --yes`) requires `--yes` because stdin is the data, not a prompt. Use `--dry-run` to preview the set (rows go to stdout) without deleting. Deleted ids lead each row, so `--format raw` pipes them onward.
- `--profile <name>` works in any token position (e.g. `untaped --profile prod awx ping`).

## Agent Guidance

- Keep stdout data-only in shell pipelines; status and warnings are on stderr.
- For automation, prefer `--format json` or `--format yaml`.
- Single-entity commands (`ping`, `save`, `<kind> update`) render a vertical detail view under `--format table` and a bare JSON object (`{…}`, not a one-element `[{…}]`) under `--format json`; collection commands (`apply`, `launch`, `jobs list`, `jobs events`/`logs`) render tables and JSON arrays.
- For human inspection, table output is fine, but do not parse it.
- Do not reveal secret fields. `$encrypted$` placeholders in saved specs mean preserve existing AWX secrets.
