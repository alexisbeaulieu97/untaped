<!--
Sync Impact Report:
- Version change: 1.0.0 → 1.1.0 (Added workspace management standards)
- Added principles: None
- Added sections: Workspace Management Standards under Architecture Requirements
- Modified sections: Architecture Requirements (new subsection with high-level principles)
- Templates requiring updates: None (no template references to update)
- Operational details: Moved specific uv commands to AGENTS.md for better separation of concerns
- Follow-up TODOs: None
-->

# untaped Constitution

## Core Principles

### I. Configuration-Driven Architecture
- All functionality MUST be configurable through YAML declarations with Jinja2 templating support.
- Libraries MUST accept configuration as structured data, not procedural code.
- Configuration files MUST be the primary interface for defining infrastructure resources, with CLI arguments supplementing but never replacing declarative configs.

**Rationale**: Infrastructure-as-Code requires declarative, version-controllable definitions that separate configuration from implementation.

### II. Validation-First Processing
- All user inputs MUST be validated using Pydantic schemas before any external API calls or resource modifications.
- Validation errors MUST provide field-specific feedback with actionable suggestions.
- No API operations SHALL proceed until configuration passes schema validation.

**Rationale**: Prevents costly API failures and provides immediate feedback to users before attempting infrastructure changes.

### III. Test-First Development (NON-NEGOTIABLE)
- TDD mandatory: Tests written → User approved → Tests fail → Then implement.
- All features MUST have contract tests, integration tests, and unit tests.
- The Red-Green-Refactor cycle MUST be strictly enforced.
- No code merges without corresponding test coverage.

**Rationale**: Infrastructure tools require extreme reliability; automated testing prevents regressions that could impact production systems.

### IV. Modular uv Workspaces
- Each functional area MUST be organized as an independent uv workspace package.
- Packages MUST be self-contained with explicit dependencies.
- For example, core utilities (untaped-core), domain logic (untaped-ansible), and CLI interface (untaped-cli) remain architecturally separated.

**Rationale**: Enables independent testing, clear dependency management, and future extensibility to other infrastructure platforms.

### V. Thin CLI Design
- CLI commands MUST follow a consistent pattern: load → render → validate → execute.
- CLIs MUST be thin wrappers around library functionality.
- All business logic MUST reside in library packages, not CLI handlers.
- Support both human-readable and structured output formats.

**Rationale**: Enables library reuse, testing isolation, and programmatic integration beyond CLI usage.

## Development Workflow

### Code Quality Gates
- Syntax validation via mypy type checking
- Style enforcement via ruff linting and formatting
- Test execution across contract, integration, unit, and performance suites
- Schema validation for all YAML processing
- API contract verification against Ansible Tower endpoints

### Testing Strategy
- Focus on three testing layers: Contract tests verify CLI and API boundaries, Integration tests validate end-to-end workflows, Unit tests ensure isolated component behavior.
- Performance tests MUST validate template rendering and schema validation under load.

### Error Handling Standards
- All errors MUST chain original exceptions using `raise ... from e`.
- Error messages MUST include actionable guidance without exposing sensitive data.
- Field-level validation errors MUST reference specific YAML paths and suggest corrections.

## Architecture Requirements

### Technology Stack Constraints
- Python 3.12+ with uv workspace management (locked)
- Typer CLI framework + Pydantic validation + Jinja2 templating (locked)
- httpx for Ansible Tower API communication (locked)
- Loguru for structured logging (locked)

### API Integration Standards
- All external API calls MUST be wrapped in dedicated service classes.
- API authentication MUST be configurable via environment variables or configuration files.
- API responses MUST be validated against expected schemas.
- Network failures MUST be handled gracefully with retry logic where appropriate.

### Workspace Management Standards
- All packages MUST use consistent dependency management practices across the workspace.
- Dependency changes MUST maintain workspace integrity and prevent version conflicts.

**Rationale**: Ensures consistent dependency resolution and prevents configuration drift across multiple packages in the workspace.

## Governance

- This Constitution supersedes all other development practices and guidelines.
- Amendments require documentation in this file, version increment following semantic versioning (MAJOR for breaking governance changes, MINOR for new principles, PATCH for clarifications), and update of all dependent templates.

- All feature implementations MUST verify constitutional compliance.
- Complexity MUST be justified against these principles.

**Version**: 1.1.0 | **Ratified**: 2025-09-21 | **Last Amended**: 2025-09-21