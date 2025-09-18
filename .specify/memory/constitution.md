# Untaped Constitution

## Core Principles

### I. Config-Driven Architecture (NON-NEGOTIABLE)
All workflow logic lives in YAML configurations and templates, never in Python code. Business logic, resource definitions, and workflow orchestration are expressed declaratively through configuration files. Python code serves only as the execution engine - interpreting configs, validating schemas, and interfacing with APIs. No hardcoded workflow logic in application code.

### II. Validation-First Processing (NON-NEGOTIABLE)
All YAML configurations must pass Pydantic schema validation before any processing begins. Schema validation occurs early in the pipeline - before any external API calls, resource modifications, or side effects. Invalid configurations fail fast with clear, actionable error messages. No configuration reaches Ansible Tower's API without successful schema validation.

### III. UV Workspace Modular Design (NON-NEGOTIABLE)
Repository is organized as UV workspaces with each package completely self-contained within a `packages/` directory. Every package has its own dependencies, tests, documentation, and clear boundaries. Packages communicate through well-defined interfaces only. Cross-package dependencies must be explicit and justified. Each workspace package serves a single, well-defined purpose.

### V. Extensible-by-Design (NON-NEGOTIABLE)
New resources, APIs, and workflows can be added without breaking existing functionality. Schema versioning supports backward compatibility during transitions. Plugin architecture allows extension without core modifications. Configuration-driven approach enables new resource types through schema additions rather than code changes. Existing workflows remain unaffected by system extensions.

## Infrastructure-as-Code Standards

### Configuration Management
- YAML is the primary configuration format for all resource definitions
- Configuration files must be version-controlled and treated as source code
- Support Jinja2 templating for dynamic configuration generation
- Environment-specific configurations managed through variable files
- No hardcoded credentials or sensitive data in configuration files
- All configurations validated through Pydantic schemas before processing

### Schema Validation Requirements
- Pydantic models define strict schemas for all YAML configurations
- Schema validation is the first step in every workflow - no exceptions
- Validation errors must provide clear, actionable feedback to users
- Schema versions are managed independently with backward compatibility
- Failed validation prevents any API calls or system modifications

### UV Workspace Management
- Use uv commands for all workspace and package management operations when available
- Prefer `uv add`, `uv remove`, `uv sync` over manual pyproject.toml editing
- Use `uv run` for script execution and `uv tool` for CLI installations
- Manual file editing allowed only when no equivalent uv command exists
- This ensures consistent dependency resolution and workspace state management

Canonical commands (examples):
```
mkdir -p packages
uv init --package --lib packages/untaped-core
uv init --package --lib packages/untaped-ansible
uv init --package --lib packages/untaped-cli

uv add --project packages/untaped-core pydantic jinja2 pyyaml
uv add --project packages/untaped-ansible pydantic httpx
uv add --project packages/untaped-cli typer rich
```

### API Integration Requirements
- All Ansible Tower API interactions must be idempotent
- Implement proper error handling with retry logic for transient failures
- Comprehensive logging of all API requests and responses for debugging

### Security & Compliance
- Credentials managed through secure credential providers (environment variables, credential files, etc.)
- All API communications must use HTTPS
- Input validation and sanitization for all user-provided data
- Audit logging for all resource modifications

## Development Workflow

### Quality Gates
- All code must pass schema validation tests before merge
- Integration tests must pass against a test Ansible Tower instance
- Code coverage minimum of 85% for all libraries
- Static analysis and linting (black, flake8, mypy) must pass
- Documentation must be updated for any API or schema changes

### Release Management
- Follow semantic versioning (MAJOR.MINOR.PATCH)
- Breaking changes require MAJOR version bump and migration guide
- Schema changes are versioned independently with backward compatibility
- All releases tagged and documented with changelog
- Support for multiple schema versions during transition periods

## Governance

This constitution supersedes all other development practices and guidelines. All pull requests and code reviews must verify compliance with these principles. Any deviation must be explicitly justified and documented.

Amendments to this constitution require:
1. Written proposal with justification and impact analysis
2. Team approval through consensus or formal vote
3. Migration plan for existing code where applicable
4. Documentation updates across all affected libraries

Complexity must always be justified - prefer simple, clear solutions over clever ones. When in doubt, choose the approach that makes the system more testable and maintainable.

**Version**: 1.0.0 | **Ratified**: September 18, 2025 | **Last Amended**: September 18, 2025