---
name: untaped-hello
description: Uses the example hello plugin through `untaped hello`. Use when demonstrating how an untaped plugin works.
---

# untaped hello

`untaped hello` is an example plugin. Use it to show how a plugin
installed beside `untaped` appears in the CLI; it does no real work.

## Setup

Settings live under `profiles.<name>.hello`: the greeting
(`untaped config set hello.greeting "hello from acme"`), the file the
message of the day comes from (`untaped config set hello.motd_path FILE`)
and how many seconds its copy is served before the file is read again
(`untaped config set hello.caches.motd.max_age_seconds 0` reads it every
time).

## Commands

| When you need to | Run |
|---|---|
| Print the configured greeting | `untaped hello greet` |
| Greet names as `hello.salutation` records, or names piped on stdin | `untaped hello say NAME...`, `untaped hello say --stdin` |
| Print the message of the day from `hello.motd_path`, refreshed once an hour by default | `untaped hello motd` |

## Workflows

1. Run `untaped plugin list` and check that `hello` is `ready`.
2. Run `untaped hello greet` and compare the output with `hello.greeting`.

## Safety

- `untaped hello greet` and `untaped hello say` only read settings and write nothing.
- `untaped hello motd` writes only its own copy of the file; `--cached`
  reads the copy without checking the file.
- Exit codes: 0 success, 1 failure, 2 usage, 4 fix the environment, 130
  interrupted.

## Pitfalls

- Read stderr as well as the rows; under `--format json` it is JSON Lines.
  Pass on what the user would want to know about, with any hint, whatever
  its `level`: a deprecated setting or flag, a skipped or partial result, a
  clamped option. Leave out progress and routine lines.
- A `hello` row that is `quarantined` means the plugin failed to load; the
  `untaped plugin list` reason says why.
