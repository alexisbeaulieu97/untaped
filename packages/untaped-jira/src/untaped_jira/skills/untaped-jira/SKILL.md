---
name: untaped-jira
description: Works with Jira Data Center or Server issues through the `untaped jira` command (JQL search, reading, creating, editing, assigning, commenting on, linking and transitioning issues, and listing projects, boards and sprints). Use when the user mentions Jira, an issue key such as OPS-123, JQL, a sprint or board, or moving an issue to another status.
---

# untaped jira

`untaped jira` reads and changes issues on Jira Data Center or self-hosted
Jira; it does not speak to Jira Cloud. Every write is visible to other people
the moment it lands, so read the issue first, preview the write, and send it
only once the user has approved that preview.

## Setup

- Settings live under `profiles.<name>.jira`. Set the server with
  `untaped config set jira.base_url https://HOST`.
- The user stores a personal access token by running `untaped auth set jira`
  in their own terminal (it prompts and keeps the token out of `config.yml`),
  or points `jira.token_command` at an argv list that prints it;
  `JIRA_API_TOKEN` is the fallback. Never ask for, print or echo a token,
  never read `~/.untaped/config.yml`, and never pass `--show-secrets`;
  `untaped auth status` says where tokens come from.
- `untaped jira whoami` checks the URL and token. A rejected token exits 4.
- Check effective defaults (`jira.assigned_jql`, `jira.default_project`,
  `jira.default_board_id`, `jira.confirm`) with `untaped config list` before
  relying on them.

## Commands

| When | Command |
|---|---|
| The user's own open issues | `untaped jira issues assigned` |
| Any other query | `untaped jira issues search --jql 'project = OPS AND status = Open'` |
| Read issues in full | `untaped jira issues get OPS-123 --comments` |
| Which transitions an issue offers now | `untaped jira issues transitions OPS-123` |
| Move an issue to another status | `untaped jira issues transition OPS-123 --to Done --dry-run` |
| Change fields or the assignee | `untaped jira issues patch OPS-123 --assignee @me --dry-run` |
| Create an issue | `untaped jira issues create --project OPS --issue-type Task --summary TEXT --dry-run` |
| Add a comment | `untaped jira issues comment OPS-123 --body TEXT --dry-run` |
| Link two issues | `untaped jira issues links create OPS-123 Blocks OPS-124 --dry-run` |
| Find a board or sprint | `untaped jira boards list --project OPS`, then `untaped jira sprints list --board-id 42` |

`--help` on any command lists its options; `--columns '?'` lists a read
command's fields.

## Workflows

### Work a ticket

1. `untaped jira issues get OPS-123 --format json`: read the status,
   assignee, description and links.
2. `untaped jira issues transitions OPS-123 --format json`: pick the
   transition whose `to_status` is the status the user wants.
3. Preview each write with `--dry-run` and show the user the preview lines.
4. After the user approves, rerun the same command with `--yes`.
5. `untaped jira issues get OPS-123`: the status, fields or comment are as
   intended.

To move every issue of a search, pipe it and preview the whole batch first:

```bash
untaped jira issues search --project OPS --status 'In Review' --format pipe \
  | untaped jira issues transition --stdin --to Done --dry-run
```

## Safety

- Preview every write with `--dry-run`, even when `jira.confirm` would not
  ask: the default policy sends creates, comments, links and add-only patches
  without a prompt. `--dry-run` sends nothing and wins over `--yes`.
- `--yes` skips the prompt. Without a terminal, a write that must ask exits 2
  unless you pass `--yes` or `--dry-run`.
- Batches over several keys (or `--stdin`) continue past a failing key, print
  `error: KEY: …` for it, and exit with the most severe failure.
- Exit codes: 0 success, 1 the request failed or was declined (missing issue,
  invalid field, no such transition), 2 fix the command line, 4 fix the
  environment (`jira.*` settings, rejected token, missing permission),
  5 Jira unavailable (retry later), 130 interrupted.
- With `--format json` stderr is JSON Lines; each error names its `category`,
  `system`, `retryable` flag and `hint`.

## Pitfalls

- `issues search` with no `--jql` and no shortcut flags searches
  `jira.assigned_jql`, not every issue.
- Transition names differ between workflows and statuses; take them from
  `issues transitions`, not from memory.
- Link direction is easy to reverse: read
  [references/writes.md#links](references/writes.md#links) before linking.
- Patch and transition previews read the issue, so a dry run needs working
  credentials; create, comment and link previews do not.
- Writes are never retried automatically. After exit 5 on a write, run
  `issues get` to see whether it landed before sending it again.

## References

| File | Read it when |
|---|---|
| [references/reading.md](references/reading.md) | searching or reading issues: how `assigned` and `search` build JQL, issue and link fields, comments, transitions, piping keys |
| [references/writes.md](references/writes.md) | creating, patching, assigning, commenting, transitioning or linking: which writes ask, what the preview shows, setting arbitrary fields, the outcome record |
