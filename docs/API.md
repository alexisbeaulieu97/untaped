# untaped API Overview

This document describes the main modules exposed by the `untaped` workspace.

## untaped_core

- `yaml_loader` – helpers for reading YAML files (`load_yaml_file`, `load_yaml_string`, `load_variables_file`).
- `template_renderer` – strict Jinja2 renderer (`render_template`).
- `validators.config_validator` – `ConfigurationValidator` orchestrates load → render → validate, returning `ValidationOutcome` objects.
- `logging` – `configure_logging` and `get_logger` wrappers around Loguru.

## untaped_ansible

- `models` – Pydantic models for job templates (`JobTemplate`), workflow job templates (`WorkflowJobTemplate`, `WorkflowNode`), and enums (`ResourceType`, `JobType`).
- `api` – httpx-based Tower client (`TowerApiClient`), auth (`TowerAuthApi`), CRUD wrappers for job templates and workflows, and resource lookup helpers.
- `services` – high-level orchestration:
  - `ConfigProcessorService` (validation pipeline)
  - `JobTemplateService` / `WorkflowJobTemplateService`
  - `ResourceValidationService`

## untaped_cli

- `app` – Typer entrypoint registering `ansible`, `create`, `update`, and `delete` subcommands.
- `common` – runtime wiring (config discovery, logging, error handling, in-memory Tower facades).
- `commands` – Typer sub-apps implementing create/update/delete flows with dry-run, version suffixing, and consistent JSON output.

## Testing

- Contract tests ensure CLI and API behaviors align with design specifications (`tests/contract/`).
- Integration tests model end-to-end quickstart scenarios (`tests/integration/`).
- Unit tests cover utilities, models, and validators (`tests/unit/`).
- Performance smoke tests guard against regressions in validation and templating (`tests/performance/`).

## Extending the API

- Add new resource models under `untaped_ansible.models` and register them in `ConfigProcessorService`.
- Implement corresponding API wrappers and services following the existing patterns.
- Update CLI commands to expose new operations, and extend contract/integration tests accordingly.
