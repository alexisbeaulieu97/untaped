# untaped-values

Install it as part of `untaped`: `uv tool install 'untaped[values]'` or `pip install 'untaped[values]'`.
Plugins that take input values depend on it, so installing one of them brings it.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped-values` is the one inputs language plugins share: a file declares the
values it needs (its inputs, each with a type, maybe a default, maybe
`secret: true`), and whoever runs the command answers them with flags, files
or a prompt. It has no commands of its own.

## Set up

Nothing to set up: it has no settings.

## Use

Give values on the command line or in a YAML file; a flag wins over a file:

```bash
untaped recipe apply acme/editorconfig ./api --var python_version=3.14 --vars-file team.yml --dry-run
```

An input that must have a value and has none is asked at a terminal, hidden
when it is a secret. With no terminal, or with `--non-interactive`, the
command names every missing input instead. Text fields render `{{ name }}`
placeholders; a secret input never goes into a path or a command line.

## Reference

For agents, `untaped docs values guides vars` prints how values are given, parsed and asked, in Markdown. Install the skill that points an agent at it with `untaped skills install values --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)).

- [Exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
