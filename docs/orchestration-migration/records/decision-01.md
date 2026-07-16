
`untaped` is a batteries-included CLI *framework* (config, profiles, themes,
consistent output, piping, HTTP/UI helpers) built on cyclopts. There is no
central `untaped` command, no plugin platform, no managed virtual environment,
no shim, and no install script.

Each tool is an **independent CLI** that depends on the `untaped` SDK and is
installed in its own `uv tool` environment:

```bash
uv tool install untaped-github
uv tool install untaped-ansible
```

PyPI package metadata is the release contract. Suite repos carry no standing
`[tool.uv.sources]` git pins (dropped 2026-07-02 once the suite was on PyPI);
release artifacts are still built with `uv build --no-sources` so wheels
declare only package ranges such as `untaped>=3.1.0,<4`.

The suite is: `github`, `jira`, `awx`, `ansible`, `workspace`, `recipe`,
`apple-health`.

Tools are versioned, installed, and released independently — possibly against
different SDK versions — but they **share two contracts**: the
`~/.untaped/config.yml` config format (see #4) and the `--format pipe` envelope
(see #3). This coupling is accepted and deliberately frozen so independently
installed tools interoperate.
