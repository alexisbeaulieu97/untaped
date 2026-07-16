+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6b2cb75a9a7cb908963b4b59c"
kind = "decision"
title = "`untaped` is an SDK, not an app — plugins retired"
created_at = "2026-07-10T00:30:02.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "git:80bb8411cd0017f3e0cde818656aaf6fd0233368:docs/decisions.md#sha256:597d74559b5447942468b7fe321ab40dccbed32e4055d9fca71830702c55831e"
+++

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
