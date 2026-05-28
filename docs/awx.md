# AWX / AAP

`untaped awx` is provided by the standalone
[`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx)
plugin. It talks to Ansible Automation Platform and upstream AWX through
their REST APIs: list/get/save/apply resources, launch and watch jobs,
inspect workflow nodes, and run declarative launch test suites.

## Install

Install both `untaped` and the AWX plugin from git:

```bash
uv tool install "git+https://github.com/alexisbeaulieu97/untaped.git" \
  --with "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git" \
  --no-sources \
  --force
```

To let `untaped plugins` remember that desired plugin state, record the
plugin without syncing, then rebuild the tool from the same source spec:

```bash
untaped plugins add "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git" --no-sync
untaped plugins sync --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git"
```

For local editable core development, point sync at the local `untaped`
checkout:

```bash
untaped plugins add "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx.git" --no-sync
untaped plugins sync --tool-spec /path/to/untaped --editable-tool
```

## Configure

```bash
untaped config set awx.base_url https://aap.example.com
untaped config set awx.token <bearer-token>
untaped awx ping
```

AAP defaults to `/api/controller/v2/`; upstream AWX users set:

```bash
untaped config set awx.api_prefix /api/v2/
```

See the plugin docs for the command reference, resource fidelity model,
YAML envelope shape, job log/event streaming, and test-suite runner:

- [`untaped-awx` README](https://github.com/alexisbeaulieu97/untaped-awx)
- [`untaped-awx` AWX docs](https://github.com/alexisbeaulieu97/untaped-awx/blob/main/docs/awx.md)
