# `untaped` — documentation

`untaped` is a batteries-included CLI **framework** (an SDK) for building
standalone DevOps tools. There is no central `untaped` command: each tool is an
independent CLI (`untaped-github`, `untaped-awx`, …) that depends on the SDK and
is installed in its own `uv tool` environment. Tools share two frozen contracts
— the `~/.untaped/config.yml` format and the `--format pipe` envelope — so they
interoperate and compose.

## Pages

- [Configuration](./configuration.md) — `~/.untaped/config.yml`,
  profiles, secrets, TLS, env-var overrides. Start here.
- [Building a tool with the untaped SDK](./plugins.md) — declare a `ToolSpec`,
  wire `run_tool`, and ship an independent CLI.
- [Agent Skills](./skills.md) — list and install Codex/Claude skills that each
  tool ships.
- [Releasing](./release.md) — PyPI/TestPyPI workflow, Trusted Publisher setup,
  and recovery rules.
- [Architecture decisions](./decisions.md) — the settled ADRs behind the
  SDK-only direction.

Command references live in each tool's own repo:

- [GitHub](https://github.com/alexisbeaulieu97/untaped-github)
- [Jira](https://github.com/alexisbeaulieu97/untaped-jira)
- [AWX / AAP](https://github.com/alexisbeaulieu97/untaped-awx)
- [Ansible](https://github.com/alexisbeaulieu97/untaped-ansible)
- [Workspaces](https://github.com/alexisbeaulieu97/untaped-workspace)
- [Recipe](https://github.com/alexisbeaulieu97/untaped-recipe)
- [Apple Health](https://github.com/alexisbeaulieu97/untaped-apple-health)

For installation, see the repo's [README](../README.md).

## Pipe-friendly by design

Row-oriented `list`/`get`/`status`-style commands support
`--format json|yaml|table|raw|pipe` and `--columns <field>` so their stdout
can feed into the next tool. `--format pipe` is a self-describing record stream
(NDJSON) that another untaped tool reads back — typed composition without
flattening to strings:

```bash
# Pick a job template interactively, then fetch its details as JSON.
untaped-awx job-templates list --format raw --columns name \
  | fzf \
  | untaped-awx job-templates get --stdin --format json

# --format pipe carries full records between independently-installed tools.
untaped-github search repos --org acme --format pipe \
  | untaped-github search code "BaseModel" --repo-stdin
```

Single-entity commands (`whoami`/`get`/`show`/`status`) render their one object
as a vertical `key: value` detail view via `emit(...)`, which dispatches by
shape — a sequence becomes a collection — and still honours every `--format`.

Side-effect commands (`<tool> profile use`, `<tool> config set`, `apply --yes`,
…) print a short confirmation to stderr and exit. **Logs go to stderr; only
data hits stdout** — so pipes stay clean. A `--quiet` root option mutes progress
and `success`/`info` messages without touching data or warnings/errors.

## Contributing / extending

Architecture, conventions, and the recipes for building a tool live in
[AGENTS.md](../AGENTS.md). It's the single source of truth for *how* `untaped`
is built — read it before sending changes.
