# Tasks: MVP Infrastructure-as-Code Toolkit

**Input**: Design documents from `/specs/001-mvp-scope-resources/`
**Prerequisites**: plan.md (required), research.md, data-model.md, contracts/

## Execution Flow (main)
```
1. Load plan.md from feature directory
   → SUCCESS: Found implementation plan with UV workspace structure
   → Extract: Python 3.11+, Typer CLI, Pydantic validation, Jinja2 templates, httpx API
2. Load optional design documents:
   → data-model.md: Extract entities → ConfigurationFile, JobTemplate, WorkflowJobTemplate, etc.
   → contracts/: CLI contracts and Tower API contracts → contract test tasks
   → research.md: Extract decisions → UV workspace setup tasks
3. Generate tasks by category:
   → Setup: UV workspaces, dependencies, linting
   → Tests: CLI contract tests, API contract tests, integration tests
   → Core: Pydantic models, template renderer, Tower API client, CLI commands
   → Integration: CLI sub-apps registration, error handling, logging
   → Polish: unit tests, performance validation, documentation
4. Apply task rules:
   → Different workspace packages = mark [P] for parallel
   → Same file = sequential (no [P])
   → Tests before implementation (TDD)
5. Number tasks sequentially (T001, T002...)
6. Generate dependency graph
7. Create parallel execution examples
8. Validate task completeness:
   → All CLI commands have contract tests ✓
   → All entities have Pydantic models ✓
   → All API integrations have tests ✓
9. Return: SUCCESS (tasks ready for execution)
```

## Format: `[ID] [P?] Description`
- **[P]**: Can run in parallel (different files/workspaces, no dependencies)
- Include exact file paths in descriptions

## Path Conventions
UV workspace structure per plan.md:
- **packages/untaped-core/**: Core utilities package
- **packages/untaped-ansible/**: Ansible Tower domain logic
- **packages/untaped-cli/**: CLI entrypoint package
- **tests/**: Test directory with contract/, integration/, unit/ subdirs

## Phase 3.1: Setup
- [ ] T001 Create UV workspace structure with packages/untaped-core/, packages/untaped-ansible/, packages/untaped-cli/ packages
- [ ] T002 Initialize packages/untaped-core package with pyproject.toml and dependencies (PyYAML, Jinja2, Pydantic)
- [ ] T003 [P] Initialize packages/untaped-ansible package with pyproject.toml and dependencies (httpx, Pydantic)
- [ ] T004 [P] Initialize packages/untaped-cli package with pyproject.toml and dependencies (Typer, Rich)
- [ ] T005 [P] Configure linting tools (black, flake8, mypy) in workspace root
- [ ] T006 [P] Setup pytest configuration with markers for contract, integration, unit tests
- [ ] T007 Create test directory structure with tests/contract/, tests/integration/, tests/unit/

## Phase 3.2: Tests First (TDD) ⚠️ MUST COMPLETE BEFORE 3.3
**CRITICAL: These tests MUST be written and MUST FAIL before ANY implementation**

### CLI Contract Tests
- [ ] T008 [P] CLI contract test for create job-template command in tests/contract/test_cli_create_job_template.py
- [ ] T009 [P] CLI contract test for create workflow-job-template command in tests/contract/test_cli_create_workflow.py
- [ ] T010 [P] CLI contract test for update job-template command in tests/contract/test_cli_update_job_template.py
- [ ] T011 [P] CLI contract test for update workflow-job-template command in tests/contract/test_cli_update_workflow.py
- [ ] T012 [P] CLI contract test for delete job-template command in tests/contract/test_cli_delete_job_template.py
- [ ] T013 [P] CLI contract test for delete workflow-job-template command in tests/contract/test_cli_delete_workflow.py

### Tower API Contract Tests
- [ ] T014 [P] Tower API contract test for job template CRUD operations in tests/contract/test_tower_api_job_templates.py
- [ ] T015 [P] Tower API contract test for workflow job template CRUD in tests/contract/test_tower_api_workflows.py
- [ ] T016 [P] Tower API contract test for authentication in tests/contract/test_tower_api_auth.py
- [ ] T017 [P] Tower API contract test for resource references (inventories, projects, credentials) in tests/contract/test_tower_api_resources.py

### Integration Tests (from Quickstart scenarios)
- [ ] T018 [P] Integration test for simple job template creation scenario in tests/integration/test_simple_job_template.py
- [ ] T019 [P] Integration test for templated job creation with variables in tests/integration/test_templated_job_creation.py
- [ ] T020 [P] Integration test for workflow job template creation in tests/integration/test_workflow_creation.py
- [ ] T021 [P] Integration test for resource update workflow in tests/integration/test_resource_update.py
- [ ] T022 [P] Integration test for resource deletion workflow in tests/integration/test_resource_deletion.py
- [ ] T023 [P] Integration test for validation error handling in tests/integration/test_validation_errors.py
- [ ] T024 [P] Integration test for template rendering errors in tests/integration/test_template_errors.py

## Phase 3.3: Core Implementation (ONLY after tests are failing)

### Pydantic Models (Data Layer)
- [ ] T025 [P] ConfigurationFile model in packages/untaped-core/src/untaped/core/models/configuration.py
- [ ] T026 [P] JobTemplate model with all fields in packages/untaped-ansible/src/untaped/ansible/models/job_template.py
- [ ] T027 [P] WorkflowJobTemplate model in packages/untaped-ansible/src/untaped/ansible/models/workflow_job_template.py  
- [ ] T028 [P] WorkflowNode model in packages/untaped-ansible/src/untaped/ansible/models/workflow_node.py
- [ ] T029 [P] VariableFile model in packages/untaped-core/src/untaped/core/models/variables.py
- [ ] T030 [P] ValidationResult and error models in packages/untaped-core/src/untaped/core/models/validation.py
- [ ] T031 [P] ResourceType and JobType enums in packages/untaped-ansible/src/untaped/ansible/models/enums.py

### Core Utilities
- [ ] T032 [P] YAML loader utility in packages/untaped-core/src/untaped/core/utils/yaml_loader.py
- [ ] T033 [P] Jinja2 template renderer in packages/untaped-core/src/untaped/core/utils/template_renderer.py
- [ ] T034 [P] Error formatter utility in packages/untaped-core/src/untaped/core/utils/error_formatter.py
- [ ] T035 [P] Configuration validator in packages/untaped-core/src/untaped/core/validators/config_validator.py

### Tower API Client
- [ ] T036 Tower API authentication client in packages/untaped-ansible/src/untaped/ansible/api/auth.py
- [ ] T037 Tower API base client with error handling in packages/untaped-ansible/src/untaped/ansible/api/base_client.py
- [ ] T038 Job template API wrapper in packages/untaped-ansible/src/untaped/ansible/api/job_templates.py
- [ ] T039 Workflow job template API wrapper in packages/untaped-ansible/src/untaped/ansible/api/workflow_job_templates.py
- [ ] T040 Resource reference API wrapper (inventories, projects, credentials) in packages/untaped-ansible/src/untaped/ansible/api/resources.py

### Service Layer  
- [ ] T041 Configuration processing service in packages/untaped-ansible/src/untaped/ansible/services/config_processor.py
- [ ] T042 Job template management service in packages/untaped-ansible/src/untaped/ansible/services/job_template_service.py
- [ ] T043 Workflow job template management service in packages/untaped-ansible/src/untaped/ansible/services/workflow_service.py
- [ ] T044 Resource validation service in packages/untaped-ansible/src/untaped/ansible/services/validation_service.py

### CLI Commands
- [ ] T045 Global CLI options and common utilities in packages/untaped-cli/src/untaped/cli/common.py
- [ ] T046 Create command implementation in packages/untaped-cli/src/untaped/cli/commands/create.py
- [ ] T047 Update command implementation in packages/untaped-cli/src/untaped/cli/commands/update.py
- [ ] T048 Delete command implementation in packages/untaped-cli/src/untaped/cli/commands/delete.py
- [ ] T049 CLI entrypoint and app registration in packages/untaped-cli/src/untaped/cli/main.py

## Phase 3.4: Integration
- [ ] T050 Register ansible CLI sub-app in main entrypoint
- [ ] T051 Configure structured logging across all packages
- [ ] T052 Implement configuration file discovery (CLI args, env vars, default paths)
- [ ] T053 Add comprehensive error handling with exit codes per CLI contract
- [ ] T054 Implement dry-run mode for all commands
- [ ] T055 Add verbose output formatting with Rich
- [ ] T056 Implement version flag for resource naming

## Phase 3.5: Polish  
- [ ] T057 [P] Unit tests for YAML loader in tests/unit/test_yaml_loader.py
- [ ] T058 [P] Unit tests for template renderer in tests/unit/test_template_renderer.py
- [ ] T059 [P] Unit tests for Pydantic models validation in tests/unit/test_models_validation.py
- [ ] T060 [P] Unit tests for error formatting in tests/unit/test_error_formatter.py
- [ ] T061 [P] Unit tests for configuration validator in tests/unit/test_config_validator.py
- [ ] T062 [P] Performance tests for schema validation <100ms in tests/performance/test_validation_performance.py
- [ ] T063 [P] Performance tests for template rendering <50ms in tests/performance/test_template_performance.py
- [ ] T064 [P] Create README.md with installation and usage instructions
- [ ] T065 [P] Create comprehensive API documentation in docs/
- [ ] T066 Remove code duplication and refactor shared utilities
- [ ] T067 Run full quickstart.md scenarios manually for validation

## Dependencies
- Setup (T001-T007) must complete before all other phases
- All tests (T008-T024) must complete and FAIL before core implementation (T025-T049)
- Models (T025-T031) should complete before services (T041-T044)
- Core utilities (T032-T035) can run parallel with models
- API wrappers (T036-T040) depend on T037 (base client)
- Services (T041-T044) depend on both models and API wrappers
- CLI commands (T045-T049) depend on services
- Integration (T050-T056) depends on CLI commands
- Polish (T057-T067) can run after integration

## Parallel Execution Examples

### Phase 3.1 Parallel Setup
```bash
# After T001, launch T002-T006 together:
Task: "Initialize packages/untaped-core package with pyproject.toml and dependencies"
Task: "Initialize packages/untaped-ansible package with pyproject.toml and dependencies" 
Task: "Initialize packages/untaped-cli package with pyproject.toml and dependencies"
Task: "Configure linting tools (black, flake8, mypy) in workspace root"
Task: "Setup pytest configuration with markers for contract, integration, unit tests"
```

### Phase 3.2 Parallel Test Writing
```bash
# Launch CLI contract tests T008-T013 together:
Task: "CLI contract test for create job-template command in tests/contract/test_cli_create_job_template.py"
Task: "CLI contract test for create workflow-job-template command in tests/contract/test_cli_create_workflow.py"
Task: "CLI contract test for update job-template command in tests/contract/test_cli_update_job_template.py"
Task: "CLI contract test for update workflow-job-template command in tests/contract/test_cli_update_workflow.py"
Task: "CLI contract test for delete job-template command in tests/contract/test_cli_delete_job_template.py"
Task: "CLI contract test for delete workflow-job-template command in tests/contract/test_cli_delete_workflow.py"
```

### Phase 3.3 Parallel Model Creation
```bash
# Launch model creation T025-T031 together:
Task: "ConfigurationFile model in packages/untaped-core/src/untaped/core/models/configuration.py"
Task: "JobTemplate model with all fields in packages/untaped-ansible/src/untaped/ansible/models/job_template.py"
Task: "WorkflowJobTemplate model in packages/untaped-ansible/src/untaped/ansible/models/workflow_job_template.py"
Task: "WorkflowNode model in packages/untaped-ansible/src/untaped/ansible/models/workflow_node.py"
Task: "VariableFile model in packages/untaped-core/src/untaped/core/models/variables.py"
Task: "ValidationResult and error models in packages/untaped-core/src/untaped/core/models/validation.py"
Task: "ResourceType and JobType enums in packages/untaped-ansible/src/untaped/ansible/models/enums.py"
```

## Validation Checklist
*GATE: Checked before task execution*

- [x] All CLI commands have corresponding contract tests (T008-T013)
- [x] All Tower API operations have contract tests (T014-T017)
- [x] All entities have Pydantic model tasks (T025-T031)
- [x] All quickstart scenarios have integration tests (T018-T024)
- [x] All tests come before implementation (Phase 3.2 before 3.3)
- [x] Parallel tasks are truly independent (different files/packages)
- [x] Each task specifies exact file path
- [x] No task modifies same file as another [P] task
- [x] Dependencies properly ordered (models → services → CLI → integration)
- [x] Constitutional requirements maintained (config-driven, validation-first, modular)

## Notes
- [P] tasks target different files/packages and have no dependencies
- All tests MUST fail before implementing corresponding functionality (TDD)
- UV workspace structure enables parallel development across packages
- Constitutional principles enforced: validation-first, config-driven, thin CLI
- Commit after each task completion for incremental progress
- Focus on making tests pass rather than over-engineering solutions