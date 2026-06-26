# Contributing

Thanks for contributing to `untaped`.

## Local Setup

```bash
uv sync
uv run pytest
uv run mypy
uv run ruff check --fix
uv run ruff format
uv run pre-commit run --all-files
```

## Documentation

Update `README.md`, `AGENTS.md`, and the relevant files under `docs/` when a
change affects the SDK surface, configuration behavior, command wiring,
profiles, themes, output, stdin, HTTP/TLS helpers, or agent-facing workflows.
The SDK ships no packaged skill; repo-owned packaged skills live with the
standalone tools and should be updated in those repos when their workflows
change.

## Sensitive Data

Do not include secrets, real customer configurations, production logs, private
workspace data, health exports, or private data in issues, tests, fixtures, or
examples. Use synthetic data for tests and examples.
