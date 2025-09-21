# Developer Guide

Developer documentation for the `untaped` Infrastructure-as-Code toolkit.

## Quick Start Commands
```bash
# Install dependencies (workspace)
uv sync

# Install dependencies (single package)
uv sync --package packages/<name>

# Type checking
uv run mypy .

# Lint and format
uv run ruff check . && uv run ruff format .

# Run tests (workspace)
uv run pytest -q

# Run tests (single package)
uv run --package packages/<name> pytest -q
```

## Technology Stack
- **Primary Language**: Python 3.12+
- **Package and Workspace Management**: uv
- **CLI Framework**: Typer
- **Schema Validation**: Pydantic
- **Settings Management**: pydantic-settings
- **Templating**: Jinja2
- **API Communication**: httpx
- **Logging**: Loguru
- **Testing**: pytest

## Code Standards

### Naming Conventions
- **Packages**: snake_case
- **Modules**: snake_case
- **Classes**: PascalCase
- **Functions**: snake_case
- **Variables**: snake_case
- **Constants**: SCREAMING_SNAKE_CASE
- **Type Aliases**: PascalCase
- **Internal Use Only**: `_snake_case` (single underscore prefix)
- **Private**: `__snake_case` (double underscore prefix)

### Documentation
- Use Google style docstrings
- Follow PEP 8 import order (standard library, third-party, local)

### Error Handling
- Always raise with context and chain the original error using `raise ... from e`
- Include actionable messages with enough data to diagnose without leaking secrets

## Dependency Management
- **Adding Dependencies**: `uv add <package>`
- **Removing Dependencies**: `uv remove <package>`
- **Package-Specific Dependencies**: `uv add --package packages/<package-name> <dependency>`
- **Development Dependencies**: `uv add --dev <package>`
- **Environment Synchronization**: `uv sync` after pulling changes

**Important**: Use uv commands instead of manually editing `pyproject.toml` files.

## Testing Guidelines

### Test Structure
- **Contract Tests**: `tests/contract/` - End-to-end CLI and API behavior
- **Integration Tests**: `tests/integration/` - Cross-package workflows
- **Unit Tests**: `tests/unit/` - Isolated component testing
- **Performance Tests**: `tests/performance/` - Load and performance validation

### Running Tests
```bash
# All tests
uv run pytest

# Specific test category
uv run pytest tests/contract -q
uv run pytest tests/integration -q
uv run pytest tests/unit -q

# Package-specific tests
uv run --package packages/<package-name> pytest
```

### Writing Tests
- Follow pytest conventions for test discovery and naming
- Use descriptive test names that explain the behavior being tested
- Follow TDD: Write failing tests first, then implement to make them pass

## Configuration Patterns
- **Application Settings**: Use `pydantic-settings` for environment-based configuration
- **YAML Configuration Files**: Use YAML for user inputs to the application
- **Template Variables**: Support Jinja2 templating in user configurations

## Development Workflow
1. **Write Tests**: Start with failing tests that describe the expected behavior
2. **Implement**: Write code to make the tests pass
3. **Validate**: Run type checking, linting, and all test suites
4. **Document**: Update docstrings and documentation as needed

## Architecture Patterns
- **Library Packages**: Use `uv init --package --lib packages/<package-name>` for reusable components
- **CLI Application Packages**: Use `uv init --package --app packages/<package-name>` for executable tools
- **Self-contained Packages**: Each package defines its own dependencies in `pyproject.toml`
- **Workspace Management**: uv automatically manages workspace dependencies and prevents conflicts

---

**Note**: For AI agent-specific guidance, see `AGENTS.md`. For architectural principles and governance, see `.specify/memory/constitution.md`.