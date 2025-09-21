# AI Agent Guidance for untaped

`untaped` is an Infrastructure-as-Code toolkit with strict architectural principles. This guidance helps AI agents understand the codebase patterns, make appropriate decisions, and avoid common mistakes when assisting with development.

## Project Mental Model

### Core Architecture Pattern
```
YAML Config → Jinja2 Render → Pydantic Validate → API Execute
```
**Never skip or reorder these steps.** This validation-first processing is constitutional and non-negotiable.

### Package Boundaries
- `untaped-core/`: Shared utilities (YAML, templates, validation, errors)
- `untaped-ansible/`: Tower-specific models, API wrappers, business logic
- `untaped-cli/`: Thin CLI layer (argument parsing, output formatting only)

**Rule**: Business logic goes in libraries, CLI handlers are thin wrappers.

## Decision Trees for Agents

### "Where Does This Code Go?"
1. **Is it shared across multiple packages?** → `untaped-core`
2. **Is it CLI argument parsing or output formatting?** → `untaped-cli`
3. **Is it something else?** → select the appropriate package or create a new one using `uv init` (`uv init --help` for options).

### "What Type of Test Do I Write?"
1. **Testing CLI behavior end-to-end?** → `tests/contract/`
2. **Testing cross-package integration?** → `tests/integration/`
3. **Testing isolated functions/classes?** → `tests/unit/`
4. **Testing performance characteristics?** → `tests/performance/`

### "How Do I Handle User Input?"
```python
# ALWAYS follow this pattern:
def process_user_input(yaml_content: str, variables: dict) -> Result:
    # 1. Load YAML
    config = load_yaml(yaml_content)

    # 2. Render templates
    rendered = render_jinja2(config, variables)

    # 3. Validate with Pydantic
    validated = ModelClass.model_validate(rendered)

    # 4. Execute business logic
    return execute_operation(validated)
```

## Critical Agent Mistakes to Avoid

### **Never Do These**
1. **Skip validation**: Don't call an API without Pydantic validation
2. **Put logic in CLI**: Don't add business logic to CLI handlers
3. **Manual pyproject.toml edits**: Use `uv` commands instead
4. **Break package boundaries**: Don't import `untaped-cli` from `untaped-core`
5. **Ignore TDD**: Don't implement before writing failing tests

### **Common Anti-Patterns**
```python
# WRONG: Direct API calls without validation
response = httpx.post(tower_url, json=user_data)

# RIGHT: Validation-first processing
validated_data = JobTemplateModel.model_validate(user_data)
response = tower_service.create_job_template(validated_data)
```

## Agent-Specific Implementation Patterns

### When Adding New CLI Commands
```python
# Template for new CLI commands:
@app.command()
def new_command(
    config_file: Path = typer.Option(...),
    dry_run: bool = typer.Option(False),
    verbose: bool = typer.Option(False)
):
    try:
        # Load config (untaped-core)
        config = load_yaml_config(config_file)

        # Business logic (untaped-ansible)
        result = service_layer.execute_operation(config, dry_run=dry_run)

        # Output formatting (untaped-cli)
        display_result(result, verbose=verbose)

    except ValidationError as e:
        display_validation_error(e)
        raise typer.Exit(1)
```

### When Adding New Pydantic Models
```python
# Always include field validation and helpful error messages
class JobTemplateModel(BaseModel):
    name: str = Field(..., description="Unique job template name")
    project: str = Field(..., description="Tower project name")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Job template name cannot be empty")
        return v.strip()
```

### When Adding New API Wrappers
```python
# Always wrap external APIs in service classes
class TowerJobTemplateService:
    def __init__(self, client: TowerAPIClient):
        self._client = client

    def create_job_template(self, template: JobTemplateModel) -> JobTemplate:
        try:
            response = self._client.post("/job_templates/", template.model_dump())
            return JobTemplate.model_validate(response.json())
        except httpx.HTTPError as e:
            raise TowerAPIError(f"Failed to create job template: {template.name}") from e
```

## Troubleshooting Workflows for Agents

### When Tests Fail
1. **Contract tests failing?** → Check CLI argument parsing and output formatting
2. **Integration tests failing?** → Check cross-package data flow and API integration
3. **Unit tests failing?** → Check individual function logic and edge cases
4. **Validation errors?** → Check Pydantic model definitions and field validators

### When Adding Dependencies
```bash
# For workspace-wide dependencies
uv add <package>

# For package-specific dependencies
uv add --package packages/untaped-core <package>

# For development dependencies
uv add --dev <package>

# Always sync after adding
uv sync --all-packages --all-groups --all-extras
```

## Agent Quality Checklist

Before suggesting code changes, verify:
- [ ] **Constitutional compliance**: Does it follow validation-first processing?
- [ ] **Package boundaries**: Is code in the right package?
- [ ] **Test coverage**: Are there tests at the appropriate level?
- [ ] **Error handling**: Are exceptions properly chained and actionable?
- [ ] **Dependencies**: Are dependencies managed via `uv`?
- [ ] **Documentation**: Are docstrings and type hints present?

## Learning the Codebase

### Key Files for Agent Understanding
- `/.specify/memory/constitution.md`: Non-negotiable principles and governance
- `/tests/contract/`: Expected CLI behavior and API contracts
- `/packages/untaped-core/src/untaped_core/`: Core utilities and patterns

### Understanding User Workflows
1. User writes YAML configuration with optional Jinja2 variables
2. CLI loads, renders, validates configuration
3. Business logic layer interacts with API
4. Results are formatted and displayed to user

---

**Remember**: This is a validation-first, configuration-driven, test-first codebase. When in doubt, validate early and often, keep packages separated, and write tests before implementation.