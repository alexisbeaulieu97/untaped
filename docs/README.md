# `untaped` — documentation

`untaped` is a personal DevOps CLI suite. One binary loads core
commands and installed domain plugins (AWX/AAP, Ansible, Jira, workspaces,
GitHub, profile, themes, ...), so daily DevOps work composes — row-oriented
`list`/`get`/`status`-style commands are designed to be piped into
`fzf`, `jq`, `awk`, or each other.

## Pages

- [Configuration](./configuration.md) — `~/.untaped/config.yml`,
  profiles, secrets, TLS, env-var overrides. Start here.
- [Plugins](./plugins.md) — install, sync, list, and diagnose optional
  plugin packages.
- [Agent Skills](./skills.md) — list and install Codex/Claude skills
  contributed by core and installed plugins.

Plugin docs and command references live in their plugin repos:

- [Workspaces](https://github.com/alexisbeaulieu97/untaped-workspace)
- [AWX / AAP](https://github.com/alexisbeaulieu97/untaped-awx)
- [Ansible](https://github.com/alexisbeaulieu97/untaped-ansible)
- [GitHub](https://github.com/alexisbeaulieu97/untaped-github)
- [Jira](https://github.com/alexisbeaulieu97/untaped-jira)
- [Profile](https://github.com/alexisbeaulieu97/untaped-profile)
- [Themes](https://github.com/alexisbeaulieu97/untaped-themes)

For installation, see the repo's [README](../README.md).

## Pipe-friendly by design

Row-oriented `list`/`get`/`status`-style commands support
`--format json|yaml|table|raw|pipe` and `--columns <field>` so their stdout
can feed into the next tool. `--format pipe` is a self-describing record stream
(NDJSON) that another untaped command reads back — typed composition without
flattening to strings:

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

# --format pipe carries full records between untaped commands (no flattening).
untaped github search repos --org acme --format pipe \
  | untaped github search code "BaseModel" --repo-stdin
```

Side-effect commands (`profile use` from the optional profile plugin,
`config set`, `apply --yes`, …)
print a short confirmation to stderr and exit. **Logs go to stderr;
only data hits stdout** — so pipes stay clean.

## Contributing / extending

Architecture, conventions, and the recipes for adding a new domain or
command live in [AGENTS.md](../AGENTS.md). It's the single source of
truth for *how* `untaped` is built — read it before sending changes.
