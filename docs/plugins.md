# Plugins

`untaped` core owns plugin discovery, desired plugin state, and the `uv`
managed virtual environment it uses to install plugins into the same Python
environment as the CLI. Plugin repos own their command references and
domain-specific docs.

## Install untaped

Install `untaped` into its managed environment first. From an `untaped`
checkout containing `scripts/install.sh`, the install script creates
`${XDG_DATA_HOME:-~/.local/share}/untaped/venv`, writes `~/.local/bin/untaped`,
and records the core package spec so later plugin syncs keep core and plugins
in one environment.

```bash
scripts/install.sh "git+https://github.com/alexisbeaulieu97/untaped.git@v0.2.0"
```

For local development, install core editable:

```bash
scripts/install.sh --editable /path/to/untaped
```

## Managed plugin state

Use `untaped plugins add` when you want `untaped` to remember the desired
plugin set in `~/.untaped/config.yml`:

```bash
untaped plugins add "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git@v0.1.1"
```

Plugin sync requires the core package spec recorded by the managed installer;
run `scripts/install.sh` before using synced plugin commands.

For multiple plugins, pass every spec to one `add` command so `untaped`
records them and exact-syncs the managed venv once:

```bash
untaped plugins add \
  "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git@v0.1.1" \
  "untaped-github @ git+https://github.com/alexisbeaulieu97/untaped-github.git@v0.2.0" \
  "untaped-ansible @ git+https://github.com/alexisbeaulieu97/untaped-ansible.git@v0.1.0" \
  "untaped-jira @ git+https://github.com/alexisbeaulieu97/untaped-jira.git@v0.1.0" \
  "untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git@v0.1.1" \
  "untaped-themes @ git+https://github.com/alexisbeaulieu97/untaped-themes.git@v0.1.0" \
  "untaped-workspace @ git+https://github.com/alexisbeaulieu97/untaped-workspace.git@v0.1.1"
```

`uv` resolves package dependencies for the managed venv from the specs recorded
in `~/.untaped/config.yml`. Managed sync ignores package-local
`[tool.uv.sources]` tables so a plugin checkout's development sources cannot
override the recorded core or plugin specs. Plugin authors should declare
required plugin dependencies in normal package metadata; dependencies that are
not available from an index must exist as their own recorded plugin specs.

When you `untaped plugins add` a **local plugin checkout**, dependencies on
other untaped plugins that the checkout maps through `[tool.uv.sources]`
(path or git entries) are auto-recorded as explicit plugin specs, recursively,
so the managed sync resolves cleanly. Each auto-recorded spec is reported and
visible in `untaped plugins list`; pass `--no-auto-deps` to opt out. Git and
index specs are never inspected this way — their source tables describe the
author's machine, not yours — so an unresolvable plugin dependency from such a
spec still fails with a hint to record it explicitly.

Direct git URLs are accepted when the plugin name can be inferred from the
repository basename. `untaped` stores the canonical `name @ url` form and
lets `plugins remove` target the normalized name.

Batch commands also accept newline-separated package specs from stdin:

```bash
untaped plugins add --stdin --no-sync < plugins.txt
untaped plugins list --format raw | untaped plugins remove --stdin --no-sync
untaped plugins sync
```

For editable plugin development, install the plugin checkout editable:

```bash
untaped plugins add --editable /path/to/untaped-profile
```

For editable multi-plugin development, record each local checkout that should be
present in the managed runtime. Repo-local `[tool.uv.sources]` entries remain
useful for that repo's own `uv sync`, but managed plugin sync does not use them.

Use `untaped plugins list` to inspect loaded and recorded plugins, and
`untaped plugins doctor` to see plugin load failures.

Plugin discovery only sees packages installed in the running interpreter's
environment. If `untaped` is launched from a foreign environment — for
example a leftover `uv tool install untaped` shim — recorded plugins exist
but none of their commands appear. Core detects this: startup prints a
one-line warning on stderr, and `untaped plugins doctor` runs a
`core-environment` check that reports when the running interpreter is not
the managed venv (or the `~/.local/bin/untaped` shim does not delegate to
it) along with the fix: re-run `scripts/install.sh`, or remove the
conflicting shim with `uv tool uninstall untaped`.

## Plugin authoring contract

Plugins expose one object through the `untaped.plugins` entry point group and
import the SDK surface from `untaped.api`. The preferred contract (api
version 3) is declarative: the object defines `id`, literal
`untaped_api_version = 3`, and `manifest()` returning a `PluginManifest`.

```python
from untaped.api import CliSpec, PluginManifest


class ExamplePlugin:
    id = "example"
    untaped_api_version = 3

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            clis=(
                # import_path defers importing the CLI module until the
                # `example` command is actually dispatched.
                CliSpec(
                    name="example",
                    import_path="untaped_example.cli:app",
                    help="Example workflows.",
                ),
            ),
            profile_settings={"example": ExampleSettings},
        )
```

Core validates the whole manifest and commits it atomically: a manifest that
conflicts with already-registered plugins (or itself) contributes nothing and
is reported by `untaped plugins doctor`. The legacy imperative contract
(`untaped_api_version = 2` with `register(registry)` calling the
`registry.add_*` hooks) is still accepted; new plugins should use manifests.

Keep the API version literal instead of importing a core constant. Future
breaking plugin API changes will increment the supported major version, and
`untaped plugins doctor` reports missing, invalid, or unsupported plugin API
versions as load errors.

Plugin CLIs follow the suite's shared command conventions and helpers from
`untaped.api`: render `--format`/`--columns` row output with `render_rows`,
wrap command bodies in `report_errors`, and reject bad usage with
`raise_usage` (exit 2). Command bodies resolve settings once via
`plugin_context(profile)` and read their section with
`ctx.section("example", ExampleSettings)`; HTTP-backed plugins build their
client with `connected_client(...)`, which validates required settings and
raises the standard "set it via `untaped config set ...`" error for missing
ones, and walk collections with `paginate_offset`/`paginate_pages`. Required inputs are required positional-only parameters
(`Parameter(help=...)` declared before `/`) — a missing value renders
`error: ... requires an argument` on stderr with exit 2 automatically; never
emulate this with an optional default plus a manual help dance.

Plugin sync does not install agent skills. After installing plugins, use
[`untaped skills list/install`](./skills.md) when you want Codex, Claude, or
another compatible agent to learn the plugin-specific workflows.

Plugins that need interactive input should use the core prompt primitives
through `ui_context(strict=False).confirm/text/secret/select/multiselect(...)`.
Those prompts require TTY stdin and render on stderr, keeping stdout available
for data streams. Plugins should not import CLI framework prompt helpers,
or `prompt_toolkit` directly, and there is no prompt-backend plugin hook yet.

## Plugin docs

- [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx) —
  Ansible Automation Platform / AWX workflows.
- [`untaped-ansible`](https://github.com/alexisbeaulieu97/untaped-ansible) —
  Ansible dependency graph and impact-analysis workflows.
- [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github) —
  authenticated user and GitHub search commands.
- [`untaped-jira`](https://github.com/alexisbeaulieu97/untaped-jira) —
  Jira Data Center ticket workflows.
- [`untaped-profile`](https://github.com/alexisbeaulieu97/untaped-profile) —
  configuration profile management.
- [`untaped-themes`](https://github.com/alexisbeaulieu97/untaped-themes) —
  terminal theme presets.
- [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace) —
  local git workspace manifests and registry state.
