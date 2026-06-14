---
name: untaped-workspace
description: Use the untaped workspace plugin.
---

# Untaped Workspace

Use this skill when the user wants an agent to operate `untaped workspace` for local multi-repo git workspaces.

## Setup

- The plugin command group is `untaped workspace`.
- A workspace has a local `untaped.yml` manifest and optional registry state under top-level `workspace` config.
- Settings include `workspace.cache_dir`, `workspace.workspaces_dir`, and registered workspace entries.
- Use `untaped workspace show --path PATH` or `--workspace NAME` to inspect a workspace before mutating it.

## Command Patterns

- `untaped workspace list` shows registered workspaces.
- `untaped workspace show` reads the manifest for one workspace.
- `untaped workspace status` reports repo branch/dirty/ahead/behind state.
- `untaped workspace sync` updates repos, and `--all` applies across registered workspaces.
- `untaped workspace foreach` runs a shell command across selected repos; use care with side effects.
- Branch commands operate on workspace manifests and git refs; inspect `--help` for exact selector behavior.

## Agent Guidance

- Prefer `--format json` for structured state and `--format raw --columns ...` for shell pipelines.
- Use `--format pipe` to chain untaped commands: it emits one self-describing record per line tagged with a `kind` (e.g. `workspace.workspace`, `workspace.repo`); `path --stdin` reads that stream back (`list --format pipe | path --stdin`).
- Do not assume the current directory is the intended workspace. Resolve target precedence: `--workspace`, then `--path`, then nearest parent `untaped.yml`.
- Treat destructive repo operations as explicit user intent. Inspect `workspace status` before broad sync or branch changes.
