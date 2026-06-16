# Migrating from the plugin runtime to the SDK-only suite

Earlier `untaped` was a single umbrella command: you installed `untaped` into a
managed virtual environment (via `scripts/install.sh`), then added plugins with
`untaped plugins add`. That whole model is retired. `untaped` is now an
[SDK](./decisions.md), and each tool is an independent CLI you install on its
own.

**Your configuration is preserved.** Tools read the same
`~/.untaped/config.yml`, and its format is unchanged (frozen as v1 — see
[`docs/decisions.md` §4](./decisions.md)). Migration is uninstalling the old
runtime and installing the tools you use; you do **not** re-enter your settings.

## 1. Remove the old managed runtime

If you installed via the managed installer, delete the shim and its managed
virtual environment:

```bash
rm -f ~/.local/bin/untaped
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/untaped/venv"
```

If you ever installed the central command as a `uv` tool, remove that too:

```bash
uv tool uninstall untaped   # only if `uv tool list` shows it
```

There is no longer an `untaped` command, no managed venv, no plugin sync, and
no `scripts/install.sh`. Removing them does not touch `~/.untaped/config.yml`.

## 2. Keep your config (one optional cleanup)

Leave `~/.untaped/config.yml` in place. Every key still applies:

- `active:`, `profiles:`, `http:`, `ui:` — SDK-owned, unchanged.
- Per-tool sections (`github:`, `jira:`, `awx:`, …) — each tool reads its own.

The only vestigial key is the old `plugins:` block (the recorded plugin set).
It is ignored now, so you can leave it or delete it:

```bash
# optional: drop the obsolete `plugins:` block
$EDITOR ~/.untaped/config.yml
```

If you point tools at a non-default config, the `UNTAPED_CONFIG` environment
variable still works.

## 3. Install the tools you use

Each tool is a standalone `uv tool` install from its git repository (PyPI
publishing is deferred; tools install and depend on the SDK via git links):

```bash
uv tool install git+https://github.com/alexisbeaulieu97/untaped-github.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-jira.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-awx.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-ansible.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-workspace.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-apple-health.git
```

Append `@<tag>` (e.g. `…untaped-github.git@v0.6.0`) to pin a specific release.
Each install is isolated, so tools can run against different SDK versions and
still interoperate through the shared config and `--format pipe` contracts.

`untaped-profile` and `untaped-themes` are **retired** — profile management
(`<tool> profile create|list|use|delete`) and theme selection (`ui.theme`) are
built into every tool.

## 4. Verify your config carried over

Run any tool you installed against its config; your existing settings should
appear (secrets redacted):

```bash
untaped-github config list
untaped-github profile list
```

Per-tool config/profile/skills now live under each command:
`untaped-github config …`, `untaped-github profile …`,
`untaped-github skills install`.
