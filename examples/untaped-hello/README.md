# untaped-hello

A minimal, tested plugin for [untaped](https://github.com/alexisbeaulieu97/untaped):
one command (`untaped hello greet`), one setting (`hello.greeting`), one
packaged skill and one `untaped setup migrate-dirs` row (moving an older
version's directory into the plugin's own). Copy this directory to start a plugin of your own.

It is not published. In a virtualenv in a copy of this directory,
`uv pip install untaped . pytest` installs `untaped` from PyPI. To test
against an unreleased core, build its wheel from the repository root and
install it by path (the copy outside the repository keeps uv from treating
it as part of the workspace):

```bash
uv build --package untaped --no-sources --out-dir dist
cp -r examples/untaped-hello /tmp/untaped-hello
uv venv /tmp/untaped-hello/.venv
uv pip install --python /tmp/untaped-hello/.venv/bin/python \
  dist/untaped-*-py3-none-any.whl /tmp/untaped-hello pytest
cd /tmp/untaped-hello
.venv/bin/untaped hello greet
.venv/bin/python -m pytest tests
```

The tests use only `untaped.testing`: `tests/conftest.py` enables the
hermetic plugin, `check_conventions("hello")` runs untaped's convention
checks, and `invoke_root` runs `untaped ...` in-process. The building guide
is [`docs/plugins.md`](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/plugins.md).
