# untaped-hello

A minimal, tested plugin for [untaped](https://github.com/alexisbeaulieu97/untaped):
one command (`untaped hello greet`), one setting (`hello.greeting`) and one
packaged skill. Copy this directory to start a plugin of your own.

It is not published. Until untaped 10 is on PyPI, install a core wheel
built from this repository by path; run this from the repository root (a
copy outside the repository keeps uv from treating it as part of the
workspace):

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

Once untaped 10 is released, `uv pip install untaped . pytest` in a copy of
this directory installs `untaped` from PyPI instead.

The tests use only `untaped.testing`: `tests/conftest.py` enables the
hermetic plugin, `check_conventions("hello")` runs untaped's convention
checks, and `invoke_root` runs `untaped ...` in-process. The building guide
is [`docs/plugins.md`](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/plugins.md).
