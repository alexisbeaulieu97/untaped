---
description: "Read before installing, syncing or running a pack you haven't vetted: what pack code runs, when, and what it can reach."
---

## Installing a pack installs code

A pack's hooks are Python that runs as you, with full file access and no
sandbox: the same trust model as `pip install`. They run whenever a plan is
computed, so on every `recipe apply`, `--dry-run` and `--check` included,
and in every golden test (`recipe test`). A dry run is no way to inspect a
pack you don't trust yet; `packs add`, `packs get`, `get`, `hooks get` and
`validate` read one without importing any of its hooks.

Hooks, scaffolding and `validate` run `uv` on the pack, so they need `uv`
on `PATH`; without it they exit 4.

## What hooks can reach

Hook workers, and the `uv` commands run on a pack (`uv run`, `uv lock`,
`uv lock --check`), get only this environment:

- `PATH`, `HOME`, `USER`/`LOGNAME`, `TZ` and temp directories;
- locale: `LANG`, `LANGUAGE`, `LC_*`;
- `UV_*` (which may hold package-index credentials) and `XDG_*`, `NETRC`;
- TLS trust: `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`,
  `CURL_CA_BUNDLE`;
- proxies: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`;
- for `git+ssh` dependencies: `SSH_AUTH_SOCK`, `GIT_SSH_COMMAND`;
- hook workers only: `PYTHONPATH`, set to the pack's `src/`.

Tokens such as `GITHUB_TOKEN` and untaped's `UNTAPED_*` credentials are not
passed. Hooks still run as you, so `~/.netrc`, untaped's `config.yml` and
git credential stores are readable. Hooks read an empty stdin; anything they
print becomes diagnostics on stderr.
