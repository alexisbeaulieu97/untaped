---
name: untaped-jira
description: Use the built-in `untaped jira` capability for Jira workflows.
---

# Untaped Jira

Use this skill when the user wants an agent to operate the `untaped jira` CLI for Jira Data Center issue workflows.

## Setup

- The command is `untaped jira`. It ships with the unified `untaped` CLI (no separate install).
- `untaped jira` targets Jira Data Center and self-hosted Jira, not Jira Cloud REST v3.
- Settings live under `profiles.<name>.jira`: `base_url`, `token`, `assigned_jql`, and optional defaults such as `default_board_id`.
- Use `untaped config set jira.token --prompt` or `--stdin` for personal access tokens, or set `jira.token_command` to an argv list that prints the token; without either, `JIRA_API_TOKEN` is used. A rejected token (HTTP 401) prints that command as a hint.
- Set the base URL with `untaped config set jira.base_url https://HOST`.

## Command Patterns

- Use `untaped jira --help` and subcommand `--help` output to confirm the available commands and flags before acting.
- Commands: `whoami`, `issues get|search|assigned|create|patch|comment|transitions|transition`, `issues comments list`, `issues links create`, `projects list|get`, `boards list`, `sprints list`.
- Jira platform calls use `/rest/api/2`; Jira Software board and sprint calls use `/rest/agile/1.0`.
- Use `untaped jira issues assigned` to list issues assigned to the authenticated Jira user. It always applies `jira.assigned_jql`; `--jql` and the shortcut flags narrow it (ANDed), and an `ORDER BY` in `--jql` replaces the default `updated DESC` order. For an unrestricted query (not limited to your assigned issues) use `untaped jira issues search --jql ...` instead.
- `untaped jira issues search` with no `--jql` or shortcut flags falls back to `jira.assigned_jql`.
- `--sprint` accepts a sprint id, a sprint name, or `openSprints()`/`futureSprints()`/`closedSprints()` (rendered as `sprint in openSprints()`).
- Use `untaped jira issues get KEY` to fetch one issue with its detail fields (`summary`, `status`, `assignee`, `updated_at`, `url`, `api_url`, plus `issue_type`, `priority`, `reporter`, `labels`, `created_at`, `resolution`, `description`, `links`, and `comments`, which is `null` unless `--comments` fetched them). `links` is always a list (empty when none) of `{key, summary, status, type, direction, relation, url}`: `direction` is `outward` or `inward` from this issue's side and `relation` is Jira's phrase for it (`blocks`, `is blocked by`); linked issues are not fetched, so `issues get` their keys for details. Search rows keep only `key`, `summary`, `status`, `assignee`, `updated_at`, `url`, and `api_url`. `url` is the browser link and `api_url` the REST link. Timestamps render in UTC as `2026-01-02T03:04:05Z`.
- `issues get` and `issues transition` accept several keys, or `--stdin` with bare keys or `--format pipe` records of kind `jira.issue` / `jira.issue_outcome` (any other kind exits 2). Each failing key prints `error: KEY: …` and the command exits 1.
- `issues comments list KEY` emits `jira.comment` records (`id`, `issue_key`, `author`, `created_at`, `updated_at`, `body`, `api_url`).
- Assign with `issues patch KEY --assignee USER` (`@me` is the authenticated user) or clear it with `--unassign`. Assignment uses the dedicated `PUT issue/KEY/assignee` endpoint, so it works even when the assignee field is not on the edit screen; combined with other field changes it sends the field edit first, then the assignment (if only the assignment fails, the error says the fields were already updated). The flags override a `fields.assignee` in `--fields-file`.
- `issues transition --resolution NAME --comment TEXT` sets a resolution and adds a comment with the transition.
- `issues links create KEY TYPE OTHER` links two issues; it reads "KEY <outward phrase> OTHER" (`OPS-1 Blocks OPS-2`: OPS-1 blocks OPS-2).
- `jira.confirm` decides which writes ask first: `destructive` (default), `always`, or `never`. Destructive writes are `issues transition` and an `issues patch` that sets a field, changes the assignee, or has an `update` operation other than `add`; `issues create`, `issues comment`, `issues links create` and add-only patches are sent without asking unless the policy is `always`. A write that asks shows a readable preview first (`METHOD path`, then `field: old → new` lines; patches and transitions read the issue's current values for it). Pass `--yes` to skip the prompt; without a terminal and without `--yes`, a write that must ask exits 2. `--dry-run` prints the same preview on stderr, emits a `planned` outcome, sends nothing, and wins over `--yes` and the policy; patch and transition dry runs read the issue, so they need working credentials (a patch dry run exits 1 if the issue cannot be read; a transition preview shows `(unknown)`, or `(not available from this status)` for an `--id` the issue does not offer).
- `issues create` / `issues patch` set arbitrary fields with `--set KEY=VALUE` and `--set-json KEY=JSON` (both repeatable), on top of an optional Jira-shaped YAML/JSON document from `--fields-file FILE` (`fields` and `update`). `issues comment --body-file FILE` reads the comment text.
- Writes emit a `jira.issue_outcome` record: `action` (`created`, `updated`, `commented`, `transitioned`, `linked`, or `planned`), `key`, `id`, `url`, `api_url`, `transition_id`, `comment_id`, `link_type`, `linked_key`.
- Prefer JSON output for issue, board, sprint, transition, project, and search workflows.
- Single-entity commands (`whoami`, `issues get KEY`, the writes on one key, `projects get`) render a vertical key:value detail view under `--format table` and a bare JSON object under `--format json`; list/search commands and multi-key calls render tables and JSON arrays.
- Usage mistakes exit 2: an issue key that is neither `PROJECT-123` nor a numeric id, or a project key that is neither `PROJECT` nor a numeric id (keys are case-insensitive and sent uppercase), a blank `--jql`, both or neither of `--to`/`--id`, `sprints list` without `--board-id` or `jira.default_board_id`, `--limit 0`.
- The JQL `issues search`/`issues assigned` POST is treated as idempotent and retries transient `429`/`503` automatically; mutating commands are never auto-retried.
- Use `--format pipe` to chain into another untaped command: it emits one self-describing record per line, each tagged with a `kind` (`jira.issue`, `jira.issue_outcome`, `jira.comment`, `jira.project`, `jira.board`, `jira.sprint`, `jira.user`, `jira.transition`).
- `--profile <name>` works in any token position (e.g. `untaped --profile work jira whoami`).
- Use configured defaults only after checking effective config with `untaped config list --format raw --columns key --columns value`.

## Agent Guidance

- Keep stdout data-only; parse `--format json` rather than table output.
- Do not assume Jira Cloud authentication or endpoints.
- Treat issue mutations such as transitions or comments as explicit user intent: preview with `--dry-run` (even when `jira.confirm` would not ask), then pass `--yes` only once the user has approved.
- Never echo tokens or raw authorization headers.
