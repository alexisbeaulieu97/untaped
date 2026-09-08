# untaped

**untaped** is one `untaped` application built on [cyclopts](https://cyclopts.readthedocs.io/). It provides the shared shell, five management command groups, and seven built-in capabilities from one install.

The root shell provides:

- **Config** — a shared `~/.untaped/config.yml` with top-level `active:` /
  `profiles:`, per-profile SDK `http` / `ui` settings, and each capability's own
  profile settings plus tool-managed top-level state.
- **Profiles** — named overlays (`dev`, `prod`, `homelab`) and a `--profile`
  root option, built in.
- **Themes** — built-in theme presets for consistent terminal styling.
- **Output** — consistent `--format json|yaml|table|raw|pipe` and `--columns`,
  so commands compose. `pipe` is a self-describing NDJSON record stream another
  `untaped` command can read back. `emit(...)` renders a single entity as a vertical
  detail view or a sequence as a collection, dispatching by shape.
- **HTTP / UI helpers** — an `HttpClient` with profile-aware TLS, automatic
  retries for transient failures (`RetryPolicy`), and pagination helpers, plus a
  `UiContext` for messages, prompts, and progress.
- **Config tooling** — `untaped doctor` diagnoses the shared file and
  `untaped config edit` opens it in `$VISUAL`/`$EDITOR`; a `--quiet` root option
  mutes progress and `success`/`info` chatter.
- **Installed version reporting** — `untaped --version` prints the installed
  distribution version.

Capability providers use the stable surface in
[`src/untaped/capability_api.py`](./src/untaped/capability_api.py). Built-ins
are composed by [`src/untaped/bootstrap.py`](./src/untaped/bootstrap.py), and
each capability owns its commands, settings, state, and packaged skill.

The public package is built with `uv build --no-sources`; the release manifest
records the supported Python floor, capability order, direct requirements, and
selected source OIDs in [`release-manifest.toml`](./release-manifest.toml).

## Requirements

Python 3.14 and [uv](https://docs.astral.sh/uv/).

## Install and use

```bash
uv tool install untaped==4.0.0
untaped --help
untaped --version
```

Every command reads the shared `~/.untaped/config.yml` and uses the same
`--format json|yaml|table|raw|pipe` output contract. A typical offline check is:

```bash
untaped config list --format yaml
untaped capabilities --format json
untaped doctor
```

## Built-in capabilities

The unified shell composes these capabilities under one executable:

- **`workspace`** — local git workspaces and repo sync.
- **`github`** — authenticated GitHub inventory, search, and corpus workflows.
- **`jira`** — Jira Data Center issue and sprint workflows.
- **`awx`** — AWX/AAP resource inspection and guarded reconciliation.
- **`ansible`** — Ansible dependency graph and impact analysis.
- **`recipe`** — local recipe pack planning, backup, and application.
- **`orchestration`** — typed repository decision and task orchestration stores.

All seven command roots resolve without network access when invoked with
`--help`:

```bash
untaped workspace --help
untaped github --help
untaped jira --help
untaped awx --help
untaped ansible --help
untaped recipe --help
untaped orchestration --help
```

See [docs/workspace/usage.md](./docs/workspace/usage.md) for the manifest
shape, command reference, and shell helper examples.

## Documentation

User-facing docs live in [`docs/`](./docs/README.md):

- [Capability authoring](./docs/plugins.md) — the stable provider surface and
  composition rules for built-in capabilities.
- [Configuration](./docs/configuration.md) — the `~/.untaped/config.yml`
  format, profiles, secrets, and TLS.
- [Agent Skills](./docs/skills.md) — how capabilities ship and install
  Codex/Claude agent skills.
- [Releasing](./docs/release.md) — PyPI/TestPyPI workflow, Trusted Publisher
  setup, and recovery rules.
- [Workspace usage](./docs/workspace/usage.md) — manifests, sync workflows, and
  shell helpers.

## Security

Please report suspected vulnerabilities privately. See
[SECURITY.md](./SECURITY.md).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) and [AGENTS.md](./AGENTS.md) for the
local workflow, architecture rules, and recipes for extending the app.

## License

MIT. See [LICENSE](./LICENSE).
