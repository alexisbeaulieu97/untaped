# Workspaces

`untaped workspace` is provided by the standalone
[`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace)
plugin. It manages local collections of git repositories through
per-workspace `untaped.yml` manifests and a local `workspace.workspaces`
registry in `~/.untaped/config.yml`.

## Install

Install both `untaped` and the workspace plugin from git:

```bash
uv tool install "git+https://github.com/alexisbeaulieu97/untaped.git" \
  --with "untaped-workspace @ git+https://github.com/alexisbeaulieu97/untaped-workspace.git" \
  --no-sources \
  --force
```

To let `untaped plugins` remember that desired plugin state, give `plugins add`
the same source spec for the core tool:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-workspace.git \
  --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git"
```

For local editable core development, point sync at the local `untaped`
checkout:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-workspace.git \
  --tool-spec /path/to/untaped \
  --editable-tool
```

See the plugin docs for the command reference, manifest shape, registry
behavior, and shell helper examples:

- [`untaped-workspace` README](https://github.com/alexisbeaulieu97/untaped-workspace)
- [`untaped-workspace` workspace docs](https://github.com/alexisbeaulieu97/untaped-workspace/blob/main/docs/workspace.md)
