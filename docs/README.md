# `untaped` — documentation

`untaped` is a personal DevOps CLI suite. One binary loads core
commands and installed domain plugins (AWX/AAP, workspaces, GitHub,
profile, ...), so daily DevOps work composes — row-oriented
`list`/`get`/`status`-style commands are designed to be piped into
`fzf`, `jq`, `awk`, or each other.

## Pages

- [Configuration](./configuration.md) — `~/.untaped/config.yml`,
  profiles, secrets, TLS, env-var overrides. Start here.
- [Plugins](./plugins.md) — install, sync, list, and diagnose optional
  plugin packages.
- [Agent Skills](./skills.md) — list and install Codex/Claude skills
  contributed by core and installed plugins.

Plugin command references live in their plugin repos:

- [Workspaces](https://github.com/alexisbeaulieu97/untaped-workspace)
- [AWX / AAP](https://github.com/alexisbeaulieu97/untaped-awx)
- [GitHub](https://github.com/alexisbeaulieu97/untaped-github)
- [Profile](https://github.com/alexisbeaulieu97/untaped-profile)

For installation, see the repo's [README](../README.md).

## Pipe-friendly by design

Row-oriented `list`/`get`/`status`-style commands support
`--format json|yaml|table|raw` and `--columns <field>` so their stdout
can feed into the next tool:

```bash
# Pick a job template interactively, then fetch its details as JSON.
untaped awx job-templates list --format raw --columns name \
  | fzf \
  | untaped awx job-templates get --stdin --format json

# With the optional workspace plugin installed, sync every workspace
# and flag anything behind upstream.
untaped workspace sync --all
untaped workspace status --all --format raw \
    --columns workspace --columns repo --columns behind \
  | awk '$3 > 0 { print }'
```

Side-effect commands (`profile use` from the optional profile plugin,
`config set`, `apply --yes`, …)
print a short confirmation to stderr and exit. **Logs go to stderr;
only data hits stdout** — so pipes stay clean.

## Contributing / extending

Architecture, conventions, and the recipes for adding a new domain or
command live in [AGENTS.md](../AGENTS.md). It's the single source of
truth for *how* `untaped` is built — read it before sending changes.
