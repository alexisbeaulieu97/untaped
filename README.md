# untaped

**untaped** is a batteries-included CLI framework for building standalone
DevOps tools on [cyclopts](https://cyclopts.readthedocs.io/). It is an SDK, not
an app: there is no central `untaped` command. You build a tool, depend on the
SDK, and ship an independent CLI.

The SDK gives every tool, for free:

- **Config** — a shared `~/.untaped/config.yml` with SDK-owned `active:`,
  `profiles:`, `http:`, and `ui:` sections plus one section per tool.
- **Profiles** — named overlays (`dev`, `prod`, `homelab`) and a `--profile`
  root option, built in.
- **Themes** — built-in theme presets for consistent terminal styling.
- **Output** — consistent `--format json|yaml|table|raw|pipe` and `--columns`,
  so commands compose. `pipe` is a self-describing NDJSON record stream another
  untaped tool can read back.
- **HTTP / UI helpers** — an `HttpClient` with profile-aware TLS and pagination
  helpers, plus a `UiContext` for messages, prompts, and progress.

You import the surface from `untaped.api` (re-exported from the `untaped`
package root), declare a `ToolSpec`, and call `run_tool(app, spec)` from your
tool's `main()`. The contract surface is the `__all__` list in
[`src/untaped/api.py`](./src/untaped/api.py).

```python
# my_tool/__main__.py
from untaped.api import create_app, run_tool, ToolSpec
from my_tool.settings import MyProfile

app = create_app(...)

def main() -> None:
    run_tool(app, ToolSpec(
        command="untaped-mytool",
        section="mytool",
        profile_model=MyProfile,
    ))
```

```toml
# pyproject.toml
[project]
dependencies = [
    # Tools depend on the SDK via a git link (PyPI publishing is deferred).
    "untaped @ git+https://github.com/alexisbeaulieu97/untaped.git@v1.0.0",
]

[project.scripts]
untaped-mytool = "my_tool.__main__:main"
```

## Requirements

Python 3.14 and [uv](https://docs.astral.sh/uv/).

## The suite

Six tools are built on the SDK. Each is an independent CLI installed into its
own `uv tool` environment:

```bash
uv tool install git+https://github.com/alexisbeaulieu97/untaped-github.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-jira.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-awx.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-ansible.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-workspace.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-apple-health.git
```

Because every tool reads the same `~/.untaped/config.yml` and shares the same
`--format pipe` envelope, independently installed tools interoperate and
compose:

```bash
untaped-awx job-templates list --format raw --columns name \
  | fzf \
  | untaped-awx job-templates get --stdin --format json
```

## Documentation

User-facing docs live in [`docs/`](./docs/README.md):

- [Building a tool with the untaped SDK](./docs/plugins.md) — the guide to
  declaring a `ToolSpec`, wiring `run_tool`, and packaging an independent CLI.
- [Configuration](./docs/configuration.md) — the `~/.untaped/config.yml`
  format, profiles, secrets, and TLS.
- [Agent Skills](./docs/skills.md) — how each tool ships and installs
  Codex/Claude agent skills.
- [Architecture decisions](./docs/decisions.md) — the settled ADRs behind the
  SDK-only direction.

Per-tool command references live in each tool's own repo:

- [GitHub](https://github.com/alexisbeaulieu97/untaped-github)
- [Jira](https://github.com/alexisbeaulieu97/untaped-jira)
- [AWX / AAP](https://github.com/alexisbeaulieu97/untaped-awx)
- [Ansible](https://github.com/alexisbeaulieu97/untaped-ansible)
- [Workspaces](https://github.com/alexisbeaulieu97/untaped-workspace)
- [Apple Health](https://github.com/alexisbeaulieu97/untaped-apple-health)

## Contributing

See [AGENTS.md](./AGENTS.md) for the architecture, hard rules, and recipes for
extending the SDK and its tools.
