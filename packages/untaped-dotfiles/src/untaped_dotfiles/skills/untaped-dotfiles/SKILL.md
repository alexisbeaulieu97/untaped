---
name: untaped-dotfiles
description: Places config files from subscribed dotfiles repos through the `untaped dotfiles` command (subscribe, enable items with a sync, once or manual policy, status, apply, sync, remove). Use when the user wants their dotfiles on a machine, asks whether a machine is behind its dotfiles repo, or mentions dotfiles or a dotfiles manifest.
---

# untaped dotfiles

A dotfiles repo carries a manifest, `dotfiles.yml`, of named items; each
item lists files placed by `link`, `copy` or `merge`. The machine
subscribes to repos, enables items with a policy, and places them with
`apply`; `sync` keeps `sync` items current and reports the rest.
`untaped dotfiles` is experimental: it may change in a minor release, so
after upgrading untaped, check a command's `--help` before relying on it.

## Setup

- Settings live under `profiles.<name>.dotfiles`: `repos_dir` (clones),
  `kept_dir` (local files set aside) and `state_dir` (`status.json`,
  `attention`). The defaults sit under `~/.untaped/dotfiles`.
- `subscribe`, `apply` and `sync` run `git`, which must be on `PATH`. Git never
  prompts for credentials, so a private repo needs a credential helper or an
  SSH agent.

## Commands

| When | Command |
|---|---|
| Add a repo | `untaped dotfiles subscribe URL` or `untaped dotfiles subscribe PATH` (a checkout, never pulled) |
| See the items a repo offers | `untaped dotfiles items` (`--all` includes items filtered out on this machine) |
| Choose items for this machine | `untaped dotfiles enable NAME --policy sync` (`manual` or `once`); `--skip FILE` leaves one file out |
| See the state of every placed path | `untaped dotfiles status` (offline); `--check` exits 3 while anything needs the user |
| See what would change | `untaped dotfiles diff`, then `untaped dotfiles apply --dry-run` |
| Place or update items | `untaped dotfiles apply NAME` (every enabled item without names) |
| Keep up to date unattended | `untaped dotfiles sync` |
| Stop managing an item | `untaped dotfiles disable NAME` (files stay) or `untaped dotfiles remove NAME` (files go) |
| Drop a repo | `untaped dotfiles unsubscribe NAME` after disabling its items |

## Workflows

1. `untaped dotfiles subscribe URL` clones the repo and lists its items;
   nothing is enabled. Read `--format json` to see suggested policies and
   which items this machine excludes.
2. `untaped dotfiles enable NAME ...` records the policy. The manifest's
   suggestion is the default, and an absent suggestion means `manual`.
3. `untaped dotfiles apply --dry-run` shows every path with its state and
   what would happen. A `foreign` path has a local file in the way: apply
   keeps it in `dotfiles.kept_dir` and the row says where. Show the user
   the plan before the real run.
4. `untaped dotfiles apply` places the paths, then `untaped dotfiles
   status` reads `applied` for each of them.

## Safety

- `apply`, `remove` and `unsubscribe` preview and ask first. Run them with
  `--dry-run`, show the user the plan, and pass `--yes` only after approval.
- `apply` refuses a path edited on this machine (`modified`, `conflict`);
  `--force` replaces it and keeps the local version aside. Never pass
  `--force` without showing the user the `diff` first.
- Exit codes: 0 success, 1 failure or declined (a `conflict` row counts), 2
  usage (including a write without a terminal and without `--yes`), 3
  something needs the user (`status --check`, `sync`), 4 fix the
  environment, 5 retry later, 130 interrupted.

## Pitfalls

- Read stderr as well as the rows; under `--format json` it is JSON Lines.
  Pass on what the user would want to know about, with any hint, whatever
  its `level`: a deprecated setting or flag, a skipped or partial result, a
  clamped option. Leave out progress and routine lines.
- Read `--format json` rather than table output.
- `apply NAME` can move other items' link files: the plan lists them as
  `moves with the clone`. `sync` never prompts and never overwrites a
  local edit; it is the command for a timer, not `apply`. When a clone is
  pulled, and what each policy does, is in
  [references/policies.md](references/policies.md).
- `remove` disables the item too, and keeps an edited copy aside rather
  than deleting it. Merged keys are taken back out of the target document.
- A registered checkout (`subscribe PATH`) is never pulled; its link files
  show whatever the working tree holds, and `repos` shows it behind.
- `status` is offline: its `behind` is as of the last `sync` or fetch.
- A directory source places each file under it separately; files other
  programs write into the target directory are never touched.

## References

| File | Read it when |
|---|---|
| [references/manifest.md](references/manifest.md) | writing or changing a manifest: entries, modes, per-machine filters, directory sources |
| [references/policies.md](references/policies.md) | deciding what `sync` and `apply` do to a path in each state, and when a clone is pulled |
