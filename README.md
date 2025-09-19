# untaped

`untaped` is a modular Infrastructure-as-Code toolkit for managing Ansible Tower job templates and workflow job templates via declarative YAML, with validation-first processing and templating support.

## Workspace Overview

- `packages/untaped-core`: shared utilities (YAML loading, template rendering, validation, logging)
- `packages/untaped-ansible`: Tower-specific models, API wrappers, and services
- `packages/untaped-cli`: Typer-based CLI (`untaped`) for create/update/delete workflows
- `tests/`: contract, integration, unit, and performance test suites

## Getting Started

```bash
# Install dependencies
uv sync

# Run contract tests
uv run pytest tests/contract -q

# Run CLI help
uv run untaped --help
```

## CLI Usage Examples

```bash
# Validate and create a job template
uv run untaped create job-template --config-file path/to/job.yml --dry-run
uv run untaped create job-template --config-file path/to/job.yml

# Update a job template
uv run untaped update job-template my-job --config-file updated.yml

# Delete a workflow job template
uv run untaped delete workflow-job-template deploy-workflow --force
```

The CLI also supports verbose output (`--verbose`), template variables (`--vars-file`, `--var KEY=VALUE`), dry-run mode, and version suffixing (`--version`).

## Configuration Flow

1. Load YAML configuration (with optional variable files and inline variables)
2. Render Jinja2 templates
3. Validate against Pydantic models
4. Resolve Tower resource references
5. Execute Tower API operations (create, update, delete)

## Documentation

- CLI quickstart workflows are documented in `specs/001-mvp-scope-resources/quickstart.md`
- Detailed tasks, research, and design decisions reside in the `specs/001-mvp-scope-resources/` directory

## Contributing

1. Run `uv run pytest` before submitting changes
2. Keep contract tests up to date with CLI/API behavior
3. Follow the modular package structure and validation-first principles
