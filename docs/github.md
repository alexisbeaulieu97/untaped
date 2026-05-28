# GitHub Plugin

`untaped github` is provided by the standalone
[`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github)
plugin. Core only supplies plugin discovery, configuration, output, and
HTTP/TLS plumbing.

## Install From Source

Install both `untaped` and the GitHub plugin from git:

```bash
uv tool install "git+https://github.com/alexisbeaulieu97/untaped.git" \
  --with "untaped-github @ git+https://github.com/alexisbeaulieu97/untaped-github.git" \
  --no-sources \
  --force
```

To let `untaped plugins` remember the desired plugin state, give `plugins add`
the same source spec for the core tool:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-github.git \
  --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git"
```

For local editable core development, point sync at the local checkout:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-github.git \
  --tool-spec /path/to/untaped \
  --editable-tool
```

After syncing, run a fresh `untaped` invocation so the newly installed
entry point is discovered.

## Configure

The plugin contributes the `github` settings section:

```bash
untaped config set github.token ghp_xxx
untaped config set github.base_url https://github.example.com/api/v3
```

`github.base_url` is only needed for GitHub Enterprise Server. See the
standalone plugin README for command reference and examples.
