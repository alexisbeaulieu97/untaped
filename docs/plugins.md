# Plugins

`untaped` core owns plugin discovery, desired plugin state, and the `uv`
tool-install command it uses to sync plugins into the installed CLI. Plugin
repos own their command references and domain-specific docs.

## Install from git

Install `untaped` with one or more plugins by passing `--with` specs to
`uv tool install`:

```bash
uv tool install "git+https://github.com/alexisbeaulieu97/untaped.git" \
  --with "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git" \
  --with "untaped-workspace @ git+https://github.com/alexisbeaulieu97/untaped-workspace.git" \
  --no-sources \
  --force
```

Run a fresh `untaped` invocation after syncing so newly installed entry
points are discovered.

## Managed plugin state

Use `untaped plugins add` when you want `untaped` to remember the desired
plugin set in `~/.untaped/config.yml`:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-awx.git \
  --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git"
```

For multiple plugins, pass every spec to one `add` command so `untaped`
records them and syncs once:

```bash
untaped plugins add \
  git+https://github.com/alexisbeaulieu97/untaped-awx.git \
  git+https://github.com/alexisbeaulieu97/untaped-github.git \
  git+https://github.com/alexisbeaulieu97/untaped-workspace.git \
  --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git"
```

Direct git URLs are accepted when the plugin name can be inferred from the
repository basename. `untaped` stores the canonical `name @ url` form and
lets `plugins remove` target the normalized name.

Batch commands also accept newline-separated package specs from stdin:

```bash
untaped plugins add --stdin --no-sync < plugins.txt
untaped plugins list --format raw | untaped plugins remove --stdin --no-sync
untaped plugins sync --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git"
```

For editable core development, point sync at the local checkout:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-profile.git \
  --tool-spec /path/to/untaped \
  --editable-tool
```

Use `untaped plugins list` to inspect loaded and recorded plugins, and
`untaped plugins doctor` to see plugin load failures.

## Plugin authoring contract

Plugins expose one object through the `untaped.plugins` entry point group. The
object must define `id`, literal `untaped_api_version = 1`, and
`register(registry)`.

```python
class ExamplePlugin:
    id = "example"
    untaped_api_version = 1

    def register(self, registry: PluginRegistry) -> None:
        registry.add_cli("example", app)
```

Keep the API version literal instead of importing a core constant. Future
breaking plugin API changes will increment the supported major version, and
`untaped plugins doctor` reports missing, invalid, or unsupported plugin API
versions as load errors.

Plugin sync does not install agent skills. After installing plugins, use
[`untaped skills list/install`](./skills.md) when you want Codex, Claude, or
another compatible agent to learn the plugin-specific workflows.

## Plugin docs

- [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx) —
  Ansible Automation Platform / AWX workflows.
- [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github) —
  authenticated user and GitHub search commands.
- [`untaped-profile`](https://github.com/alexisbeaulieu97/untaped-profile) —
  configuration profile management.
- [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace) —
  local git workspace manifests and registry state.
