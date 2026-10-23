# untaped-hello

A minimal, tested plugin for [untaped](https://github.com/alexisbeaulieu97/untaped):
a command (`untaped hello greet`) and its setting (`hello.greeting`), a
record (`Salutation` in `records.py`, kind `hello.salutation`) that `untaped hello
say` writes and reads back with `--stdin` (`accepts=(Salutation,)`), a
stub skill (`untaped skills install hello` writes it, and it sends the agent
to `untaped agents hello`) and one `untaped setup migrate-dirs` row (moving an older
version's directory into the plugin's own). `untaped hello motd` serves a
file through a cache (`MOTD` in `settings.py`, listed on the spec, so
`hello.caches.motd.max_age_seconds` tunes it), the way a plugin keeps what an
upstream answered. Copy this directory to start a plugin of your own.

It is not published. In a virtualenv in a copy of this directory,
`uv pip install 'untaped[lint]' . pytest` installs `untaped` and its `lint`
plugin from PyPI. To test against an unreleased core, build the wheels from
the repository root and install them by path (the copy outside the repository keeps uv from treating
it as part of the workspace):

```bash
uv build --package untaped --no-sources --out-dir dist
uv build --package untaped-lint --no-sources --out-dir dist
cp -r examples/untaped-hello /tmp/untaped-hello
uv venv /tmp/untaped-hello/.venv
uv pip install --python /tmp/untaped-hello/.venv/bin/python \
  dist/untaped-*-py3-none-any.whl dist/untaped_lint-*-py3-none-any.whl \
  /tmp/untaped-hello pytest
cd /tmp/untaped-hello
.venv/bin/untaped hello greet
.venv/bin/python -m pytest tests
```

The tests use `untaped.sdk.testing` and `untaped_lint.testing`: `tests/conftest.py`
enables the hermetic plugin, `assert_clean("hello")` fails where `untaped lint`
would, and `invoke_root` runs `untaped ...` in-process. The building guide
is [`docs/plugins.md`](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/plugins.md).
