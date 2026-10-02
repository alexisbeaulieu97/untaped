# untaped-hello

A minimal, tested plugin for [untaped](https://github.com/alexisbeaulieu97/untaped):
one command (`untaped hello greet`), one setting (`hello.greeting`) and one
packaged skill. Copy this directory to start a plugin of your own.

It is not published. To try it beside a local `untaped`:

```bash
uv venv
uv pip install untaped . pytest
.venv/bin/untaped hello greet
.venv/bin/python -m pytest tests
```

The tests use only `untaped.testing`: `tests/conftest.py` enables the
hermetic plugin, `check_conventions("hello")` runs untaped's convention
checks, and `invoke_root` runs `untaped ...` in-process. The building guide
is [`docs/plugins.md`](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/plugins.md).
