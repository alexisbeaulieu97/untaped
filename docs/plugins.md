# Plugins

`untaped` core owns plugin discovery, desired plugin state, and the `uv`
tool-install command it uses to sync plugins into the installed CLI. Plugin
repos own their command references and domain-specific docs.

## Install from git

Install `untaped` with one or more plugins by passing tag-qualified `--with`
specs to `uv tool install`. These packages are distributed through GitHub
tags and releases for now, not PyPI.

```bash
uv tool install "git+https://github.com/alexisbeaulieu97/untaped.git@v0.1.2" \
  --with "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git@v0.1.1" \
  --with "untaped-workspace @ git+https://github.com/alexisbeaulieu97/untaped-workspace.git@v0.1.1" \
  --no-sources \
  --force
```

Run a fresh `untaped` invocation after syncing so newly installed entry
points are discovered.

## Managed plugin state

Use `untaped plugins add` when you want `untaped` to remember the desired
plugin set in `~/.untaped/config.yml`:

```bash
untaped plugins add "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git@v0.1.1" \
  --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git@v0.1.2"
```

For multiple plugins, pass every spec to one `add` command so `untaped`
records them and syncs once:

```bash
untaped plugins add \
  "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git@v0.1.1" \
  "untaped-github @ git+https://github.com/alexisbeaulieu97/untaped-github.git@v0.2.0" \
  "untaped-ansible @ git+https://github.com/alexisbeaulieu97/untaped-ansible.git@v0.1.0" \
  "untaped-jira @ git+https://github.com/alexisbeaulieu97/untaped-jira.git@v0.1.0" \
  "untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git@v0.1.1" \
  "untaped-themes @ git+https://github.com/alexisbeaulieu97/untaped-themes.git@v0.1.0" \
  "untaped-workspace @ git+https://github.com/alexisbeaulieu97/untaped-workspace.git@v0.1.1" \
  --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git@v0.1.2"
```

`untaped-ansible` depends on `untaped-github>=0.2.0`, so install both
together when using GitHub tags:

```bash
untaped plugins add \
  "untaped-github @ git+https://github.com/alexisbeaulieu97/untaped-github.git@v0.2.0" \
  "untaped-ansible @ git+https://github.com/alexisbeaulieu97/untaped-ansible.git@v0.1.0" \
  --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git@v0.1.2"
```

Direct git URLs are accepted when the plugin name can be inferred from the
repository basename. `untaped` stores the canonical `name @ url` form and
lets `plugins remove` target the normalized name.

Batch commands also accept newline-separated package specs from stdin:

```bash
untaped plugins add --stdin --no-sync < plugins.txt
untaped plugins list --format raw | untaped plugins remove --stdin --no-sync
untaped plugins sync --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git@v0.1.2"
```

For editable core development, point sync at the local checkout:

```bash
untaped plugins add /path/to/untaped-profile \
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

Plugins that need interactive input should use the core prompt primitives
through `ui_context(strict=False).confirm/text/secret/select/multiselect(...)`.
Those prompts require TTY stdin and render on stderr, keeping stdout available
for data streams. Plugins should not import `typer.prompt`, `typer.confirm`,
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
