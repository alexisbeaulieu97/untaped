---
name: untaped-git
description: Explains untaped's Git plumbing through the `untaped git` command (which plugin supplies credentials for a Git host with `git hosts`, the credential helper untaped writes into its worktrees, and the repo store other plugins keep repositories in). Use when a Git fetch or push in an untaped worktree asks for credentials or fails to authenticate, or when the user asks where untaped keeps repositories or which Git version it needs.
---

# untaped git

Plugins that know a Git host supply credentials and a proxy for it; untaped
uses them when it fetches, and the worktrees it creates ask
`untaped git credential` for them. The repo store keeps one shared bare
repository per remote, which other plugins fetch into and add worktrees
from.

## Setup

- Git 2.29 or newer; `untaped doctor` checks it (`git.version`).
- Settings live under `profiles.<name>.git`: `store_dir` (the repo store,
  default `~/.untaped/plugins/git/store`) and `untaped_helper_first`.

## Commands

| When | Command |
|---|---|
| See which plugin answers for each host | `untaped git hosts` |
| Check what a worktree's helper would answer | `printf 'protocol=https\nhost=github.com\n\n' \| untaped git credential get` |

## Workflows

1. A push from an untaped worktree fails to authenticate: run
   `untaped git hosts`. A host with no plugin uses your own Git
   credentials; `credential` false means the plugin has no token (set it
   with `untaped auth set`).
2. `helpers_first` names helpers your own Git config asks before untaped.
   If one holds a stale password, set `git.untaped_helper_first: true`;
   untaped's worktrees then ask only untaped for that host.

## Safety

- `untaped git credential get` prints a secret on stdout: Git reads it.
  Never run it where its output is logged.
- Never edit a repo under the store by hand: other plugins' refs and
  worktrees live in the same repository.

## Pitfalls

- Two plugins claiming one host is a configuration error (exit 4) until
  one is ranked first; the error names the rank command.
- Refs under `refs/untaped/` belong to other plugins; they show up in
  `git log --all` from an untaped worktree.

## References

- `untaped git hosts --help` and `untaped git credential --help` list each
  command's options.
