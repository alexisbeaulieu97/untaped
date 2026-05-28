# `untaped` — documentation

`untaped` is a personal DevOps CLI suite. One binary aggregates a
handful of domain sub-apps (workspaces, AWX/AAP, GitHub, …) so daily
DevOps work composes — commands that emit data are designed to be
piped into `fzf`, `jq`, `awk`, or each other.

## Pages

- [Configuration](./configuration.md) — `~/.untaped/config.yml`,
  profiles, secrets, TLS, env-var overrides. Start here.
- [Workspaces](./workspace.md) — manage local git workspaces (collections
  of repos): manifests, the registry, `sync` / `status` / `foreach`,
  the `uwcd` shell helper.
- [AWX / AAP](./awx.md) — talk to Ansible Automation Platform:
  list / save / apply resources, launch jobs, run declarative test
  suites.
- [GitHub](./github.md) — inspect the authenticated GitHub user
  (intentionally narrow today).

For installation, see the repo's [README](../README.md).

## Pipe-friendly by design

Every data-emitting command (`list`, `get`, `status`, …) supports
`--format json|yaml|table|raw` and `--columns <field>` so its stdout
can feed into the next tool:

```bash
# Pick a job template interactively, then fetch its details as JSON.
untaped awx job-templates list --format raw --columns name \
  | fzf \
  | untaped awx job-templates get --stdin --format json

# Morning routine: sync every workspace, flag anything behind upstream.
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
