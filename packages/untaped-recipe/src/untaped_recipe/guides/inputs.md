---
description: "Read before passing inputs to `recipe apply`: where each input's value comes from, how values are parsed, and when it prompts."
---

## Where a value comes from

For each input, the first of these that gives a value wins:

1. `--var`, `--vars-file` or `--input-from`;
2. the recipe's `from` candidates;
3. the recipe's `default`;
4. a prompt, for a required input only;
5. otherwise a `missing required input` error.

An input name the recipe doesn't declare is
rejected. `untaped recipe get REF` lists a recipe's inputs.

## How values are parsed

- `--var` parses its value as YAML only for inputs declared `list` or
  `dict` (`--var 'cols=[name, owner]'`); a scalar input takes the literal
  string.
- `--vars-file` values are YAML, so `3.10` becomes `3.1`: quote
  version-like strings.

## Values per target

`--input-from NAME='<expression>'` derives one input per target. It must
resolve for every target, where a recipe's `from` candidates fall through
silently to the next. It can't be combined with `--var` for the same input,
and an input with `scope: global` rejects it.

Expressions (a recipe's `from` and `--input-from`) allow literal text,
constants and field access on `target.path`, `target.name`,
`target.parent_path`, `target.parent_name` and `record` (the piped record
that named the target). Filters, operators, calls and control blocks are
rejected. With `--stdin`, upstream output can choose both the targets and
their values.

```console
$ untaped recipe apply acme/readme ./api ./web --input-from 'title={{ target.name }}' --dry-run
```

## Prompts

Only a required input with no value prompts, and only when stdin is a
terminal; a sensitive input prompts hidden, and `list`/`dict` inputs never
prompt. Prompts run in target order before planning starts.

With no terminal (piped `--stdin` included), `--non-interactive` or
`--check`, a missing input fails with `missing required input: NAME`: a
global input fails the run, a target input fails that target's row.
