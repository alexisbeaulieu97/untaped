# Apply details: previews, inputs and targets

## Previews

- The preview goes to stderr. `apply` and `--dry-run` default to
  `--preview table` (changed files, absolute paths, change kind, line
  counts); `--check` defaults to `--preview none`, the summary line only.
- `--preview diff` prints unified diffs that `patch` accepts. It is the only
  full-detail view once a table collapses.
- A table collapses from per-file to per-target rows past the
  `recipe.preview_max_rows` setting (default 50, `0` for unlimited), then
  truncates with an exact `showing first N of M targets` count.
- Targets that resolve a sensitive input show no file detail or diff, even
  with `--preview diff`. Their values appear as `***` in rows, warnings,
  errors and backup metadata; templates and hooks still get the real value.

## Failures and concurrency

- A target that fails to plan or write is reported and writes nothing; the
  other targets proceed. Within one target, writes are a transaction that
  rolls back on failure.
- `--parallel N` (`-j`, at most 32) plans targets concurrently and sizes the
  hook worker pool.
- `--hook-timeout SECONDS` bounds each hook call (`0` disables). Preparing a
  pack's hook environment has its own bound,
  `recipe.hook_startup_timeout_seconds` (default 300), and prints
  `preparing hook environment...` on stderr.

## Inputs

- Precedence per input: `--var`/`--vars-file` or `--input-from` → the
  recipe's `from` → the recipe's `default` → a prompt (required inputs only)
  → a `missing required input` error.
- Among fixed values, a later `--vars-file` wins over an earlier one and
  `--var` wins over every file. Unknown input names are rejected.
- `--var` parses the value as YAML only for inputs declared `list` or
  `dict` (`--var 'cols=[name, owner]'`); scalar inputs take the literal
  string.
- `--input-from NAME='<jinja>'` derives one input per target. It must
  resolve for every target, while a recipe's `from` candidates fall through
  silently. It cannot be combined with `--var` for the same input, and
  `scope: global` inputs reject it.
- The expression sandbox (recipe `from` and `--input-from`) allows literal
  text, constants and field access on `target.path`, `target.name`,
  `target.parent_path`, `target.parent_name` and `record`. Filters,
  operators, calls and control blocks are rejected.

## Prompts

- Only a required input with no value prompts, and only when stdin is a
  terminal; sensitive inputs prompt hidden. `list`/`dict` inputs never
  prompt.
- With no terminal (piped `--stdin` included), `--non-interactive` or
  `--check`, a missing input fails with `missing required input: NAME`. A
  global input fails the run; a target input fails that target's row.
- Prompts run in target order before planning starts. Ctrl-C at a prompt
  exits 130.

## Targets from stdin

- `--stdin` replaces positional directories; passing both is a usage error.
  The confirmation then reads the controlling terminal, and input prompts
  never run.
- A line is a path unless it is a JSON object carrying the untaped envelope
  marker; a directory named `2024` is still a path.
- A record's target is its absolute `target_path`, else `path`. Records whose
  `kind` ends in `.summary` are skipped. Repo records such as
  `workspace.repo` must carry `target_path` or are rejected before planning.
- The same directory given twice, in any spelling, is planned once.
- `from` expressions can read the target's `record`, so upstream output can
  choose both the targets and their input values.
