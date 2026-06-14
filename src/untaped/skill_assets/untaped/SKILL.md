---
name: untaped
description: Use the untaped CLI, configuration model, plugin system, and shared command conventions.
---

# Untaped

Use this skill when working in an `untaped` repo or when the user asks an agent to operate the `untaped` CLI.

## CLI Model

- `untaped` is the core binary. Domain commands such as `awx`, `workspace`, `github`, `profile`, `jira`, and `ansible` are plugins registered through the `untaped.plugins` Python entry point group.
- New plugin entry point objects normally expose `id`, literal `untaped_api_version = 3`, and `manifest() -> PluginManifest`.
- API v4 uses the same manifest contract and may also contribute root options and a settings layout. API v2 imperative plugins are legacy compatibility only.
- Use `untaped plugins list` to inspect recorded and loaded plugins, and `untaped plugins doctor` to diagnose plugin load failures.
- Install or update plugin packages with `untaped plugins add`, `untaped plugins remove`, and `untaped plugins sync`; these commands manage the untaped virtual environment, not agent skills.
- Install agent-facing guidance with `untaped skills list` and `untaped skills install`; this is explicit and separate from plugin sync.

## Configuration

- User config lives in `~/.untaped/config.yml` unless `UNTAPED_CONFIG` is set.
- Profile selection is plugin-owned: `untaped-profile` contributes the root `--profile` option and profiles settings layout. Without that plugin, config is treated as a flat document.
- Profile-scoped settings live under `profiles.<name>`. Top-level plugin state such as `plugins`, `ui`, or workspace registries is loaded from the same file.
- Use `untaped config list` to inspect effective settings. Secret fields are redacted unless the owning command exposes an explicit reveal option.
- Prefer `untaped config set KEY --prompt` or `--stdin` for secret values so they do not appear in shell history; `--prompt` uses the setting schema to choose hidden input, visible text, or a select menu.

## Output And Automation

- Prefer `--format json` or `--format yaml` for structured automation.
- Prefer `--format raw --columns ...` for shell pipelines.
- Use `--format pipe` to chain one untaped command into another: it emits a
  self-describing record stream (NDJSON) that a downstream command reads back
  via `--stdin`/`--repo-stdin` (or `read_records`), preserving full records
  instead of flattening to identifier strings.
- Treat stdout as data. Status, warnings, prompts, and progress belong on stderr.
- Repeated selector flags in plugins are usually additive; inspect `--help` before assuming singular behavior.

## Development Workflow

- Read the nearest `AGENTS.md` before editing a repo.
- When changing command behavior, settings, workflows, plugin contracts, or major docs, update any repo-owned packaged `SKILL.md` in the same change.
- In plugin repos, keep CLI code thin: parse arguments, build use cases, call them, then format output.
- Core owns plugin plumbing, configuration loading, stdin/output helpers, HTTP/TLS helpers, and shared errors. Profile selection is contributed by `untaped-profile`.
- Plugins own their domain commands, domain settings models, docs, and packaged agent skills.
