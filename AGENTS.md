# Repository Guidelines

`untaped` is a modular Python toolkit designed to simplify Infrastructure-as-Code (IaC) operations. Instead of manually configuring resources or writing brittle one-off scripts, `untaped` enables engineers to manage resources through YAML configurations, enriched with Jinja2 templating and Pydantic validation.

## Quick Start
- Install deps (workspace): `uv sync`
- Install deps (single package): `uv sync --package packages/<name>`
- Type check: `uv run mypy .`
- Lint/format: `uv run ruff check . && uv run ruff format .`
- Tests (workspace): `uv run pytest -q`
- Tests (single package): `uv run --package packages/<name> pytest -q`

## Project Overview

### Technology Stack
- **Primary Language**: Python 3.12+
- **Package and Workspace Management**: uv
- **CLI Framework**: Typer
- **Schema Validation**: Pydantic
- **Settings Management**: pydantic-settings
- **Templating**: Jinja2
- **API Communication**: requests
- **Logging**: Loguru
- **Testing**: pytest

## Project Structure

```
untaped/
    packages/
        <package-name>/
            pyproject.toml
            src/
            tests/
    tests/
    pyproject.toml
```

## Development Environment Setup

### Prerequisites
- Python 3.12+
- uv
- git

## Installation Steps
```bash
# Sync the virtual environment with the main workspace
uv sync

# Sync the virtual environment with a specific package
uv sync --package [package-path]

# Sync the virtual environment with all packages, extras, and groups
uv sync --all-packages --all-extras --all-groups
```

## Available Commands
Available commands are defined per package in the `[project.scripts]` section of thei respective `pyproject.toml` file.

Commands can be run using:
```bash
# From main workspace
uv run [command-name]

# From specific package
uv run --package [package-path] [command-name]
```

## Testing Guidelines

### Running Tests
```bash
# Running tests in the main workspace
uv run pytest

# Running tests in a specific package
uv run --package [package-path] pytest
```

### Test Structure
- **Unit Tests**: tests should be written in the `tests` directory of the respective package

### Writing Tests
- **Testing Framework**: pytest
- Follow standard pytest conventions for test discovery and naming
- Use descriptive test names that explain the behavior being tested

## Code Standards and Style

### Coding Conventions
- **Naming**:
    - **Packages**: snake_case
    - **Modules**: snake_case
    - **Classes**: PascalCase
    - **Functions**: snake_case
    - **Variables**: snake_case
    - **Constants**: SCREAMING_SNAKE_CASE
    - **Type Aliases**: PascalCase
    - **Internal Use Only**: snake_case with a prefix of `_` (e.g. `_internal_use_only`)
    - **Private**: snake_case with a prefix of `__` (e.g. `__private_variable`)
- **Documentation**: Use Google style docstrings
- **Import Organization**: Follow PEP 8 import order (standard library, third-party, local)

## Error Handling
- Always raise with context and chain the original error using `raise ... from e`
- Include actionable messages with enough data to diagnose without leaking secrets

## Architecture and Patterns
- **Library Packages**: Use `uv init --package --lib packages/<package-name>` for reusable components
- **CLI Application Packages**: Use `uv init --package --app packages/<package-name>` for executable tools
- Each package is self-contained with its own dependencies defined in `pyproject.toml`
- `uv` automatically manages workspace dependencies and prevents conflicts

## Configuration Patterns
- **Application Settings**: Use `pydantic-settings` for environment-based configuration
- **YAML Configuration Files**: Use YAML for user inputs to the application

## Review Checklist
When implementing features, ensure:
- [ ] Code follows established patterns
- [ ] Tests are included and passing
- [ ] Documentation is updated
