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

For multiple plugins, record them first, then sync once:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-awx.git --no-sync
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-github.git --no-sync
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-workspace.git --no-sync
untaped plugins sync --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git"
```

Direct git URLs are accepted when the plugin name can be inferred from the
repository basename. `untaped` stores the canonical `name @ url` form and
lets `plugins remove` target the normalized name.

For editable core development, point sync at the local checkout:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-profile.git \
  --tool-spec /path/to/untaped \
  --editable-tool
```

Use `untaped plugins list` to inspect loaded and recorded plugins, and
`untaped plugins doctor` to see plugin load failures.

## Plugin docs

- [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx) —
  Ansible Automation Platform / AWX workflows.
- [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github) —
  authenticated user and GitHub search commands.
- [`untaped-profile`](https://github.com/alexisbeaulieu97/untaped-profile) —
  configuration profile management.
- [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace) —
  local git workspace manifests and registry state.
