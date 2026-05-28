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
  --force
```

To let `untaped plugins` remember the desired plugin state, record the
plugin without syncing, then rebuild the tool from the same source spec:

```bash
untaped plugins add "untaped-github @ git+https://github.com/alexisbeaulieu97/untaped-github.git" --no-sync
untaped plugins sync --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git"
```

For local editable core development, point sync at the local checkout:

```bash
untaped plugins add "untaped-github @ git+https://github.com/alexisbeaulieu97/untaped-github.git" --no-sync
untaped plugins sync --tool-spec /path/to/untaped --editable-tool
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
