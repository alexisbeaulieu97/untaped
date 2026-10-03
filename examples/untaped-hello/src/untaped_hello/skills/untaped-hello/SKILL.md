---
name: untaped-hello
description: Uses the example hello capability through `untaped hello`. Use when demonstrating how an untaped plugin works.
---

# untaped hello

`untaped hello` is an example plugin. Use it to show how a capability
installed beside `untaped` appears in the CLI; it does no real work.

## Setup

Settings live under `profiles.<name>.hello`. The only one is the greeting:
`untaped config set hello.greeting "hello from acme"`.

## Commands

| When you need to | Run |
|---|---|
| Print the configured greeting | `untaped hello greet` |

## Workflows

1. Run `untaped capabilities` and check that `hello` is `ready`.
2. Run `untaped hello greet` and compare the output with `hello.greeting`.

## Safety

- `untaped hello greet` only reads settings and writes nothing.
- Exit codes: 0 success, 1 failure, 2 usage, 4 fix the environment, 130
  interrupted.

## Pitfalls

- Read stderr as well as the rows; under `--format json` it is JSON Lines.
  Pass on what the user would want to know about, with any hint, whatever
  its `level`: a deprecated setting or flag, a skipped or partial result, a
  clamped option. Leave out progress and routine lines.
- A `hello` row that is `quarantined` means the plugin failed to load; the
  `untaped capabilities` reason says why.
