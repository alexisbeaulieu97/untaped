# Tasks: GitHub API File Operations

**Input**: Design documents from `/specs/002-let-s-add/`
**Prerequisites**: plan.md (required), research.md, data-model.md, contracts/

## Execution Flow (main)
```
1. Load plan.md from feature directory
   → SUCCESS: Found implementation plan with UV workspace structure
   → Extract: Python 3.12+, gh CLI integration, Pydantic validation, Jinja2 templates
2. Load optional design documents:
   → data-model.md: Extract entities → FileOperation, Repository, FilePath, VariableFile, ValidationResult
   → contracts/: CLI contracts and gh API contracts → contract test tasks
   → research.md: Extract decisions → gh CLI setup tasks
3. Generate tasks by category:
   → Setup: UV workspaces, dependencies, linting
   → Tests: CLI contract tests, gh API contract tests, integration tests
   → Core: Pydantic models, gh CLI wrapper, CLI commands
   → Integration: CLI sub-apps registration, error handling, logging
   → Polish: unit tests, performance, docs
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
   → All gh API integrations have tests ✓
9. Return: SUCCESS (tasks ready for execution)
```

## Format: `[ID] [P?] Description`
- **[P]**: Can run in parallel (different files/workspaces, no dependencies)
- Include exact file paths in descriptions

## Path Conventions
UV workspace structure per plan.md:
- **packages/untaped-core/**: Core utilities package (shared models, validators)
- **packages/untaped-ansible/**: Ansible Tower domain logic (existing)
- **packages/untaped-github/**: GitHub API domain logic (new)
- **packages/untaped-cli/**: CLI entrypoint package
- **tests/**: Test directory with contract/, integration/, unit/ subdirs
Modules under these packages use underscore import names (e.g., `untaped_core`, `untaped_github`, `untaped_cli`).

## Phase 3.1: Setup
- [x] T001 Create new untaped-github UV workspace package structure with packages/untaped-github/ directory
- [x] T002 Initialize packages/untaped-github package with pyproject.toml and core dependencies (subprocess, pydantic)
- [x] T003 [P] Configure linting and formatting with Ruff plus mypy settings in untaped-github package
- [x] T004 [P] Setup pytest configuration with markers for contract, integration, unit tests in untaped-github

## Phase 3.2: Tests First (TDD) ⚠️ MUST COMPLETE BEFORE 3.3
**CRITICAL: These tests MUST be written and MUST FAIL before ANY implementation**

### CLI Contract Tests
- [x] T005 [P] CLI contract test for read-file command in tests/contract/test_cli_read_file.py
- [x] T006 [P] CLI contract test for list-directory command in tests/contract/test_cli_list_directory.py
- [x] T007 [P] CLI contract test for authentication validation in tests/contract/test_cli_auth.py

### gh API Contract Tests
- [x] T008 [P] gh API contract test for file content retrieval in tests/contract/test_gh_api_file_read.py
- [x] T009 [P] gh API contract test for directory listing in tests/contract/test_gh_api_directory_list.py
- [x] T010 [P] gh API contract test for authentication errors in tests/contract/test_gh_api_auth_errors.py

### Integration Tests (from User Scenarios)
- [x] T011 [P] Integration test for file reading scenario in tests/integration/test_file_read_scenario.py
- [x] T012 [P] Integration test for directory listing scenario in tests/integration/test_directory_list_scenario.py
- [x] T013 [P] Integration test for templated file paths in tests/integration/test_templated_paths.py
- [x] T014 [P] Integration test for error handling scenarios in tests/integration/test_error_scenarios.py
- [x] T015 [P] Integration test for authentication failures in tests/integration/test_auth_failures.py

## Phase 3.3: Core Implementation (ONLY after tests are failing)

### Pydantic Models (Data Layer)
- [x] T016 [P] FileOperation model in packages/untaped-github/src/untaped_github/models/file_operation.py
- [x] T017 [P] Repository model in packages/untaped-github/src/untaped_github/models/repository.py
- [x] T018 [P] FilePath model in packages/untaped-github/src/untaped_github/models/file_path.py
- [x] T019 [P] VariableFile model in packages/untaped-github/src/untaped_github/models/variable_file.py
- [x] T020 [P] ValidationResult and error models in packages/untaped-github/src/untaped_github/models/validation.py

### Core Utilities
- [x] T021 [P] gh CLI wrapper utility in packages/untaped-github/src/untaped_github/gh_cli_wrapper.py
- [x] T022 [P] Configuration validator in packages/untaped-github/src/untaped_github/validators/config_validator.py
- [x] T023 [P] Error formatter utility in packages/untaped-github/src/untaped_github/error_formatter.py

### gh API Client
- [x] T024 gh CLI authentication client in packages/untaped-github/src/untaped_github/api/auth.py
- [x] T025 gh CLI base wrapper with error handling in packages/untaped-github/src/untaped_github/api/base.py
- [x] T026 File operations API wrapper in packages/untaped-github/src/untaped_github/api/file_operations.py

### Service Layer
- [x] T027 Configuration processing service in packages/untaped-github/src/untaped_github/services/config_processor.py
- [x] T028 File operation service in packages/untaped-github/src/untaped_github/services/file_service.py
- [x] T029 Authentication validation service in packages/untaped-github/src/untaped_github/services/auth_service.py

### CLI Commands
- [x] T030 Global CLI options and common utilities in packages/untaped-cli/src/untaped_cli/github_common.py
- [x] T031 File read command in packages/untaped-cli/src/untaped_cli/commands/github_read.py
- [x] T032 Directory list command in packages/untaped-cli/src/untaped_cli/commands/github_list.py
- [x] T033 CLI entrypoint and Typer app registration in packages/untaped-cli/src/untaped_cli/github_app.py

## Phase 3.4: Integration
- [x] T034 Register github CLI sub-app in main entrypoint
- [x] T035 Configure structured logging across untaped-github package
- [x] T036 Implement configuration file discovery (CLI args, env vars, default paths)
- [x] T037 Add comprehensive error handling with exit codes per CLI contract
- [x] T038 Implement dry-run mode for all commands
- [x] T039 Add verbose output formatting with Rich
- [x] T040 Implement gh CLI authentication verification

## Phase 3.5: Polish
- [x] T041 [P] Unit tests for Pydantic models validation in tests/unit/test_models_validation.py
- [x] T042 [P] Unit tests for gh CLI wrapper in tests/unit/test_gh_cli_wrapper.py
- [x] T043 [P] Unit tests for configuration validator in tests/unit/test_config_validator.py
- [x] T044 [P] Unit tests for error formatting in tests/unit/test_error_formatter.py
- [x] T045 [P] Performance tests for schema validation <100ms in tests/performance/test_validation_performance.py
- [x] T046 [P] Performance tests for gh CLI execution <5s in tests/performance/test_gh_cli_performance.py
- [x] T047 [P] Create README.md with installation and usage instructions for GitHub support
- [x] T048 [P] Create comprehensive CLI documentation in docs/github-commands.md
- [x] T049 Remove code duplication and refactor shared utilities
- [x] T050 Run full integration test scenarios manually for validation

## Dependencies
- Setup (T001-T004) must complete before all other phases
- All tests (T005-T015) must complete and FAIL before core implementation (T016-T033)
- Models (T016-T020) should complete before services (T027-T029)
- Core utilities (T021-T023) can run parallel with models
- API wrappers (T024-T026) depend on T025 (base wrapper)
- Services (T027-T029) depend on both models and API wrappers
- CLI commands (T030-T033) depend on services
- Integration (T034-T040) depends on CLI commands
- Polish (T041-T050) can run after integration

## Parallel Execution Examples

### Phase 3.1 Parallel Setup
```bash
# After T001, launch T002-T004 together:
Task: "Initialize packages/untaped-github package with pyproject.toml and core dependencies"
Task: "Configure linting and formatting with Ruff plus mypy settings in untaped-github package"
Task: "Setup pytest configuration with markers for contract, integration, unit tests in untaped-github"
```

### Phase 3.2 Parallel Test Writing
```bash
# Launch CLI contract tests T005-T007 together:
Task: "CLI contract test for read-file command in tests/contract/test_cli_read_file.py"
Task: "CLI contract test for list-directory command in tests/contract/test_cli_list_directory.py"
Task: "CLI contract test for authentication validation in tests/contract/test_cli_auth.py"
```

### Phase 3.3 Parallel Model Creation
```bash
# Launch model creation T016-T020 together:
Task: "FileOperation model in packages/untaped-github/src/untaped_github/models/file_operation.py"
Task: "Repository model in packages/untaped-github/src/untaped_github/models/repository.py"
Task: "FilePath model in packages/untaped-github/src/untaped_github/models/file_path.py"
Task: "VariableFile model in packages/untaped-github/src/untaped_github/models/variable_file.py"
Task: "ValidationResult and error models in packages/untaped-github/src/untaped_github/models/validation.py"
```

## Notes
- [P] tasks target different files/packages and have no dependencies
- All tests MUST fail before implementing corresponding functionality (TDD)
- UV workspace structure enables parallel development across packages
- Constitutional principles enforced: validation-first, config-driven, gh CLI integration
- Commit after each task completion for incremental progress
- Focus on making tests pass rather than over-engineering solutions

## Task Generation Rules
*Applied during main() execution*

1. **From Requirements**:
   - Each FR (FR-001 to FR-009) → corresponding test and implementation tasks
   - Each user scenario → integration test task [P]

2. **From Entities**:
   - Each entity → Pydantic model creation task [P]
   - Relationships → service layer tasks

3. **From User Stories**:
   - Each acceptance scenario → integration test [P]
   - Each edge case → error handling test scenario

4. **Ordering**:
   - Setup → Tests → Models → Services → CLI → Integration → Polish
   - Dependencies block parallel execution

## Validation Checklist
*GATE: Checked by main() before returning*

- [x] All CLI commands have corresponding contract tests (T005-T007)
- [x] All gh API operations have contract tests (T008-T010)
- [x] All entities have Pydantic model tasks (T016-T020)
- [x] All user scenarios have integration tests (T011-T015)
- [x] All tests come before implementation (Phase 3.2 before 3.3)
- [x] Parallel tasks are truly independent (different files/packages)
- [x] Each task specifies exact file path
- [x] No task modifies same file as another [P] task
- [x] Dependencies properly ordered (models → services → CLI → integration)
- [x] Constitutional requirements maintained (config-driven, validation-first, modular)
