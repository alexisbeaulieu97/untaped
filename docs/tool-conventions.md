# Capability conventions

This page covers built-in capability packages inside the unified `untaped`
repository. The root app is assembled by `src/untaped/bootstrap.py`; a
capability owns its directory, config section, state model, doctor checks, and
packaged skills.

## Layout and boundaries

Use this layout under `src/untaped/capabilities/<name>/`:

```text
<name>/
|-- __init__.py       # SPEC plus nullary build_app()
|-- cli/              # Cyclopts parsing and output wiring
|-- application/      # use cases and orchestration
|-- domain/           # entities and pure business rules
|-- infrastructure/   # HTTP, filesystem, subprocess, and API adapters
|-- settings.py       # profile and optional disjoint state models
`-- skills/           # packaged agent skills
```

Keep the import direction `cli -> application -> domain` and
`infrastructure -> domain`. Domain modules do not import CLI, application, or
infrastructure code. Cross-capability private-helper imports are forbidden by
default. A narrow, explicitly justified exception is allowed only when it
matches an entry in [`docs/dependency-policy.toml`](./dependency-policy.toml),
which is the module-prefix allow-list. Keep any exception narrow; broadly
shared behavior belongs in core and must be exposed through the stable provider
surface when external providers need it.

Every source module starts with a module docstring that states what it owns.
Re-export-only `__init__.py` files are exempt. Use absolute imports and keep
provider-facing imports on `untaped.capability_api`; its `__all__` is the
closed external contract.

## Development and verification

Run checks from the repository root:

```bash
uv sync
uv run pre-commit install
uv run pytest
uv run mypy
uv run ruff check --fix
uv run ruff format
uv run untaped --help
uv run untaped <capability> --help
```

The full pytest run enforces the coverage gate. If a formatter or fixer mutates
files, review the resulting diff before committing.

## Output

stdout is data. Use `emit(...)` for a single entity or a collection so output
formats and pipe envelopes stay consistent. Use `UiContext` for prompts,
progress, and semantic messages on stderr. Reserve raw stderr output for data
that a caller explicitly requested there.

Use `render_rows(...)` when a command needs the rendered string; use `emit(...)`
for a command that returns one entity. Machine-readable JSON, YAML, raw, and
pipe formats must not depend on the active human theme.

For `--format raw` without `--columns`, the first key of each row is emitted.
Put the row identifier first in list records and preserve that order as part of
the command contract.

## Pipe records

The pipe envelope is documented in [plugins.md](./plugins.md#5-piping). Its
wire version is `1`, and kinds use the lowercase capability namespace plus a
snake-case noun:

```text
<capability>.<noun>
<capability>.<noun>.summary
```

The `.summary` suffix is reserved for informational records. A capability owns
its domain kind table; do not centralize those kinds in core documentation.

## Capability-local ownership

Keep domain rules, identifier fields, kind tables, domain gotchas, and release
specifics with the capability that owns them. Keep config layout, profiles,
env-var overrides, root management commands, pipe grammar, and skill install
mechanics in the core docs.

A packaged skill is a source artifact under
`src/untaped/capabilities/<name>/skills/<full-id>/`. When command behavior,
settings, workflows, or contracts change, update the owning skill in the same
change. Preserve its full `SkillAsset.name`; users may use a short selector,
but the installed ID and marker remain stable.

See [Capability authoring](./plugins.md) for provider examples and
[the composition specification](./capabilities-spec.md) for validation and
entry-point rules.
