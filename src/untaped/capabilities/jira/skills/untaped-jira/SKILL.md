---
name: untaped-jira
description: Use the untaped-jira CLI.
---

# Untaped Jira

Use this skill when the user wants an agent to operate the `untaped jira` CLI for Jira Data Center ticket workflows.

## Setup

- The command is `untaped jira`. It ships with the unified `untaped` CLI (no separate install).
- V1 targets Jira Data Center and self-hosted Jira, not Jira Cloud REST v3.
- Settings live under `profiles.<name>.jira`: `base_url`, `token`, `assigned_jql`, and optional defaults such as `default_board_id`.
- Use `untaped config set jira.token --prompt` or `--stdin` for personal access tokens.
- Set the base URL with `untaped config set jira.base_url https://HOST`.

## Command Patterns

- Use `untaped jira --help` and subcommand `--help` output to confirm the available V1 surface before acting.
- Jira platform calls use `/rest/api/2`; Jira Software board and sprint calls use `/rest/agile/1.0`.
- Use `untaped jira issue assigned` to list tickets assigned to the authenticated Jira user. It uses `jira.assigned_jql` unless `--jql` is passed.
- Use `untaped jira issue get KEY` to fetch one concise ticket row by key or id.
- Prefer JSON output for issue, board, sprint, transition, project, and search workflows.
- Single-entity commands (`me`, `issue get`/`create`/`edit`/`comment`/`transition`, `project get`) render a vertical key:value detail view under `--format table` and a bare JSON object (`{…}`, not a one-element `[{…}]`) under `--format json`; list/search commands render tables and JSON arrays.
- The JQL `issue search`/`issue assigned` POST is treated as idempotent and retries transient `429`/`503` automatically; mutating commands (`create`/`comment`/`transition`) are never auto-retried.
- Use `--format pipe` to chain into another untaped command: it emits one self-describing record per line, each tagged with a `kind` (`jira.issue`, `jira.project`, `jira.board`, `jira.sprint`, `jira.user`, `jira.comment`, `jira.transition`).
- `--profile <name>` works in any token position (e.g. `untaped --profile work jira me`).
- Use configured defaults only after checking effective config with `untaped config list --format raw --columns key --columns value`.

## Agent Guidance

- Keep stdout data-only; parse `--format json` rather than table output.
- Do not assume Jira Cloud authentication or endpoints.
- Treat ticket mutations such as transitions or comments as explicit user intent.
- Never echo tokens or raw authorization headers.
