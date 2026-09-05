# Contributing

Thanks for contributing to `untaped-workspace`.

## Local Setup

```bash
uv sync
uv run pytest
uv run mypy
uv run ruff check --fix
uv run ruff format
uv run untaped-workspace --help
uv run pre-commit run --all-files
```

## Documentation

Update `README.md`, `AGENTS.md`, and
`src/untaped_workspace/skills/untaped-workspace/SKILL.md` when a change
affects command behavior, settings, workflows, output contracts, or
agent-facing usage.

## Sensitive Data

Do not include secrets, real customer configurations, private workspace
manifests, production logs, health exports, or private data in issues, tests,
fixtures, or examples. Use synthetic data for tests and examples.
