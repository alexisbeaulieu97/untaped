# `untaped` — documentation

`untaped` is a single application composing built-in capabilities. Use the
installed command's `--help` output for the current command surface; these pages
cover the workflows and contracts that are useful across commands.

## Pages

- [AWX mutation integrity](./awx-mutations.md) — prepared writes, conflict checks,
  verification and partial outcomes.
- [Configuration](./configuration.md) — `~/.untaped/config.yml`,
  profiles, secrets, TLS, env-var overrides. Start here.
- [Building a capability provider](./plugins.md) — package an external
  capability for the unified CLI.
- [Agent Skills](./skills.md) — list and install the skills shipped with the
  composed application.
- [Releasing](./release.md) — PyPI/TestPyPI workflow, Trusted Publisher setup,
  and recovery rules.
- [Workspace usage](./workspace/usage.md) — workspace manifests, sync, and
  shell helpers.

For installation, see the repo's [README](../README.md).

## Pipe-friendly by design

Row-oriented `list`/`get`/`status`-style commands support
`--format json|yaml|table|raw|pipe` and `--columns <field>` so their stdout
can feed into the next command. `--format pipe` is a self-describing record stream
(NDJSON) that another `untaped` command reads back — typed composition without
flattening to strings:

```bash
# Pick a job template interactively, then fetch its details as JSON.
untaped awx job-templates list --format raw --columns name \
  | fzf \
  | untaped awx job-templates get --stdin --format json

# --format pipe carries full records between capability commands.
untaped github search repos --org acme --format pipe \
  | untaped github search code "BaseModel" --repo-stdin
```

Single-entity commands (`whoami`/`get`/`show`/`status`) render a readable detail
view by default and still honour every `--format`.

Side-effect commands (`untaped profile use`, `untaped config set`, `apply --yes`,
…) print a short confirmation to stderr and exit. **Logs go to stderr; only
data hits stdout** — so pipes stay clean. A `--quiet` root option mutes progress
and `success`/`info` messages without touching data or warnings/errors.

## Contributing / extending

Contribution rules live in [AGENTS.md](../AGENTS.md). Source code and tests are
the authority for implementation behavior.
