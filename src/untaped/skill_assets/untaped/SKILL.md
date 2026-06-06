---
name: untaped
description: Use the untaped CLI, configuration model, plugin system, and shared command conventions.
---

# Untaped

Use this skill when working in an `untaped` repo or when the user asks an agent to operate the `untaped` CLI.

## CLI Model

- `untaped` is the core binary. Domain commands such as `awx`, `workspace`, `github`, `profile`, `jira`, and `ansible` are plugins registered through the `untaped.plugins` Python entry point group.
- Plugin entry point objects must expose `id`, literal `untaped_api_version = 1`, and `register(registry)`.
- Use `untaped plugins list` to inspect recorded and loaded plugins, and `untaped plugins doctor` to diagnose plugin load failures.
- Install or update plugin packages with `untaped plugins add`, `untaped plugins remove`, and `untaped plugins sync`; these commands manage the uv tool environment, not agent skills.
- Install agent-facing guidance with `untaped skills list` and `untaped skills install`; this is explicit and separate from plugin sync.

## Configuration

- User config lives in `~/.untaped/config.yml` unless `UNTAPED_CONFIG` is set.
- Profile-scoped settings live under `profiles.<name>`. Top-level plugin state such as `plugins`, `ui`, or workspace registries is loaded from the same file.
- Use `untaped config list` to inspect effective settings. Secret fields are redacted unless the owning command exposes an explicit reveal option.
- Prefer `untaped config set KEY --prompt` or `--stdin` for secret values so they do not appear in shell history.

## Output And Automation

- Prefer `--format json` or `--format yaml` for structured automation.
- Prefer `--format raw --columns ...` for shell pipelines.
- Treat stdout as data. Status, warnings, prompts, and progress belong on stderr.
- Repeated selector flags in plugins are usually additive; inspect `--help` before assuming singular behavior.

## Development Workflow

- Read the nearest `AGENTS.md` before editing a repo.
- In plugin repos, keep CLI code thin: parse arguments, build use cases, call them, then format output.
- Core owns plugin plumbing, config/profile resolution, stdin/output helpers, HTTP/TLS helpers, and shared errors.
- Plugins own their domain commands, domain settings models, docs, and packaged agent skills.
