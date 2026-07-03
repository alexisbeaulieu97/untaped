# Fleet tool conventions

This page is for agents and contributors working inside an `untaped-<tool>`
repo. It captures the suite-wide conventions that should not be recopied into
each tool's `AGENTS.md`.

## Layering

Suite tools use a four-layer DDD layout:

```text
src/untaped_<tool>/
|-- cli/             # Cyclopts commands; thin command parsing and output wiring.
|-- application/     # Use cases and orchestration; ports live in application/ports.py.
|-- domain/          # Entities and value objects; pure business rules, no I/O.
`-- infrastructure/  # External adapters: HTTP, filesystem, subprocesses, and APIs.
```

The import direction is `cli -> application -> domain` and
`infrastructure -> domain`. Domain modules must not import CLI, application, or
infrastructure code. Application use cases depend on protocols declared in
`application/ports.py`, which lets tests pass stubs without importing httpx,
reading config files, or touching external systems.

## Code norms

Every source module starts with a module docstring that states what the module
owns. Re-export-only `__init__.py` files are exempt because they are plumbing,
not behavior.

Use absolute imports. Ruff enforces `ban-relative-imports = "all"` in suite
repos, and tests follow the same rule. Tools import the SDK only from
`untaped.api`; SDK internals can move without notice.

Use `uv` commands for dependency changes, such as `uv add`,
`uv add --group dev`, and `uv lock --upgrade-package untaped`. Hand-edit tool
configuration in `pyproject.toml` when needed, but do not hand-edit dependency
lists or lockfile contents.

## Dev workflow & verification

Use this workflow in tool repos:

```bash
uv sync
uv run pre-commit install
uv run pytest
uv run mypy
uv run ruff check --fix
uv run ruff format
uv run untaped-<tool> --help
```

For tight loops, `uv run pytest --no-cov ...` is acceptable because it skips the
coverage plugin overhead. The full `uv run pytest` run enforces the repo's
coverage gate and must be part of the final verification loop.

Every change ends with the verification loop. If a command mutates files, such
as `ruff check --fix` or `ruff format`, review the resulting diff before
committing.

## Output conventions

stdout is data only. Command data goes through `emit(...)`, which renders one
model or mapping as a single detail object and a sequence as a collection. It
writes the rendered result to stdout itself.

stderr is chrome: progress, prompts, semantic messages, diagnostics, and
intentional stderr data such as previews or diff bodies. Use `UiContext` for
messages, prompts, and progress; reserve `echo(..., err=True)` for raw stderr
data, not normal status messaging.

Use `render_rows(...)` only when a command needs the rendered string instead of
immediate stdout output. In SDK 3.0, `render_rows` validates pipe kinds and
returns a string. `table` output resolves the active UI theme through
`ui_context()`, while `json`, `yaml`, `raw`, and `pipe` render through an
unthemed `UiContext` so machine-readable output stays stable.

For `--format raw` without `--columns`, the first key of each row is emitted.
Every list-style use case must put the row identifier first so shell pipelines
receive the useful value by default. Reordering row keys is a contract change.
Pin the generic behavior with a local `tests/unit/test_format_raw_first_key.py`
test; the specific identifier fields remain tool-local.

## Pipe records

The `--format pipe` envelope is documented in
[`docs/plugins.md` section 7](./plugins.md#7-piping). SDK 3.0 validates pipe
kinds at emit time. The generic grammar is `<tool>.<noun>`, where `<tool>` is
the lowercase tool namespace and `<noun>` is lowercase snake_case. The optional
`.summary` suffix is reserved for informational summary records, so
`<tool>.<noun>.summary` is allowed but arbitrary third segments are not.

Each tool documents its own kind table locally. Do not centralize domain kind
tables in the SDK docs.

## What stays tool-local

Keep domain hard rules, kind tables, identifier lists, identifier-field choices,
domain gotchas, and release specifics in the tool repo that owns them.

Repo-owned packaged skills are source artifacts. When command behavior,
settings, workflows, tool contracts, or major docs change, update the tool's
packaged `SKILL.md` in the same change.
