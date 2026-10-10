# untaped

`untaped` is one CLI for config, profiles, themes, consistent output, typed
piping and HTTP/TLS, with one command subtree per plugin. This package is
the core: the CLI shell, the plugin SDK (`untaped.sdk`) and the management
commands. It is the only package that ships the `untaped` command.

Plugins are extras of this package:

```bash
uv tool install 'untaped[all]'   # or 'untaped[github]', 'untaped[awx]', ...
pip install 'untaped[all]'
```

See the [project README](https://github.com/alexisbeaulieu97/untaped#readme)
for the extras, install and usage, and
[Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md)
for the first commands.
