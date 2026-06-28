---
name: untaped-workspace
description: Use the untaped-workspace CLI.
---

# Untaped Workspace

Use this skill when the user wants an agent to operate `untaped-workspace` for local multi-repo git workspaces.

## Setup

- The command is `untaped-workspace`.
- A workspace has a local `untaped.yml` manifest and registry state.
- Profile settings are `cache_dir` and `workspaces_dir`, addressed bare (e.g. `untaped-workspace config set cache_dir ...`); the `workspaces` name→path registry is tool-managed state written by `adopt`/`init`/`forget`, not a setting.
- Use `untaped-workspace show --path PATH` or `--workspace NAME` to inspect a workspace before mutating it.

## Command Patterns

- `untaped-workspace list` shows registered workspaces.
- `untaped-workspace show` reads the manifest for one workspace.
- `untaped-workspace status` reports repo branch/dirty/ahead/behind state.
- `untaped-workspace sync` updates repos, and `--all` applies across registered workspaces. Use `-j N` / `--parallel N` for up to `N` concurrent repo sync jobs; the cap is global across selected repos, not per workspace.
- `untaped-workspace sync --prune` removes safe orphan clones after sync jobs finish and skips unsafe, uninspectable, or symlinked orphans with row-level `skip` outcomes. It does not prompt and has no `--yes`.
- `untaped-workspace foreach` runs a shell command across selected repos; use care with side effects.
- Branch commands operate on workspace manifests and git refs; inspect `--help` for exact selector behavior.

## Agent Guidance

- Prefer `--format json` for structured state and `--format raw --columns ...` for shell pipelines.
- Use `--format pipe` to chain commands: it emits one self-describing record per line tagged with a `kind` (e.g. `workspace.workspace`, `workspace.repo`); `path --stdin` reads list streams back (`untaped-workspace list --format pipe | untaped-workspace path --stdin`). `show --format pipe` repo rows keep `path` as the workspace root and include `target_path` for the concrete repo checkout; an empty workspace emits a `workspace.summary` row with no `target_path`.
- The SDK provides `--quiet`/`-q` to mute the spinner and success/info lines (errors and data still print), plus `config doctor` and `config edit` for diagnosing and editing the active config.
- Do not assume the current directory is the intended workspace. Resolve target precedence: `--workspace`, then `--path`, then nearest parent `untaped.yml`.
- Treat destructive repo operations as explicit user intent. Inspect `untaped-workspace status` before broad sync or branch changes.
- Sync uses a central bare cache plus `git clone --reference` for missing clones. Existing clones fetch/pull their own working remotes and do not touch the bare cache; deleting or corrupting the cache can damage referenced clones.
- Sync branch metadata is clone-time only. Use `untaped-workspace branch apply` to switch existing clones; sync skips dirty, diverged, or wrong-branch repos instead of checking them out.
- Prune safety is local-only. `sync --prune` skips, and `remove --prune` / `forget --prune` refuse, when a clone has dirty/untracked/staged work, stash entries, or commits/local tags not reachable from local remote-tracking refs. The prune check does not fetch; stale remote-tracking refs are trusted as the offline safety boundary. `forget --prune` checks declared repo paths and immediate child `.git` directories before deleting the workspace directory.
- `sync -j N` is not host-aware, and Ctrl-C can wait for in-flight git subprocesses until their timeout.
