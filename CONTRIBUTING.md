# Contributing

Thanks for contributing to `untaped`.

## Local setup

```bash
uv sync
uv run pytest
uv run mypy
uv run ruff check --fix
uv run ruff format
uv run pre-commit run --all-files
```

## Documentation and capabilities

Update the owning concept page under `docs/` when a change affects the root
shell, capability composition, configuration, profiles, themes, output, stdin,
HTTP/TLS helpers, or agent workflows. Keep capability-specific command and
settings guidance with the capability that owns it, and link to the core pages
for shared mechanics.

Built-in capability skills are source artifacts in
`src/untaped/capabilities/<name>/skills/<full-id>/`. Update the owning
`SKILL.md` in the same change when its command behavior, settings, workflow,
or contract changes. Preserve the full skill ID; root installation accepts a
short selector but writes the stable full ID and marker. An external provider
owns the packaged skills shipped in its own distribution.

## Sensitive data

Do not include secrets, real customer configurations, production logs, private
workspace data, health exports, or other private data in issues, tests, fixtures,
or examples. Use synthetic data for tests and examples.
