# untaped-hello

A minimal, tested plugin for [untaped](https://github.com/alexisbeaulieu97/untaped),
in the 12.0 shape: the plugin is a class, `Hello` in `plugin.py`, and what it
has are its attributes. Its commands are classes, one module each under
`commands/`: `Greet` (`untaped hello greet`) reads its setting
(`hello.greeting`), `Say` writes a record (`Salutation` in `records.py`, kind
`hello.salutation` because `Say` prints it) and reads it back with `--stdin`,
and `Motd` serves a file through a cache (`motd = MOTD` on the class, so
`hello.caches.motd.max_age_seconds` tunes it), the way a plugin keeps what an
upstream answered. The class also has a stub skill (`untaped skills install
hello` writes it, and it sends the agent to `untaped agents hello`). The entry
point names the class, `hello = "untaped_hello.plugin:Hello"`, in a leaf
module so the rest of the package imports without the commands. Copy this
directory to start a plugin of your own.

It is not published, and the plugin class it uses is not in a released
`untaped` yet: until the next major it runs only against a core built from this
repository. Build the wheels from the repository root and install them by
path (the copy outside the repository keeps uv from treating it as part of
the workspace):

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

The tests mirror the package (`tests/untaped_hello/commands/test_greet.py`
tests `commands/greet.py`) and use `untaped.sdk.testing` and
`untaped_lint.testing`: `tests/conftest.py` enables the hermetic plugin,
`assert_clean("hello")` fails where `untaped lint` would, and the `install`
fixture composes `Hello` into the test's home: `install(Hello).run(Greet())`
runs a command, `.cli("hello say ada")` a command line, and the `clock`
fixture ages the cache. `check(install(Hello))` runs the composition checks
and `assert_surface(Hello)` keeps `tests/untaped_hello/snapshots/surface.txt`.
The building guide is [`docs/plugins.md`](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/plugins.md).
