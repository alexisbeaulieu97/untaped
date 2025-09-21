
# Implementation Plan: GitHub API File Operations

**Branch**: `002-github-api-support` | **Date**: September 21, 2025 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-let-s-add/spec.md`

## Execution Flow (/plan command scope)
```
1. Load feature spec from Input path
   → If not found: ERROR "No feature spec at {path}"
2. Fill Technical Context (scan for NEEDS CLARIFICATION)
   → Detect Project Type from context (web=frontend+backend, mobile=app+api)
   → Set Structure Decision based on project type
3. Fill the Constitution Check section based on the content of the constitution document.
4. Evaluate Constitution Check section below
   → If violations exist: Document in Complexity Tracking
   → If no justification possible: ERROR "Simplify approach first"
   → Update Progress Tracking: Initial Constitution Check
5. Execute Phase 0 → research.md
   → If NEEDS CLARIFICATION remain: ERROR "Resolve unknowns"
6. Execute Phase 1 → contracts, data-model.md, quickstart.md, agent-specific template file (e.g., `CLAUDE.md` for Claude Code, `.github/copilot-instructions.md` for GitHub Copilot, `GEMINI.md` for Gemini CLI, `QWEN.md` for Qwen Code or `AGENTS.md` for opencode).
7. Re-evaluate Constitution Check section
   → If new violations: Refactor design, return to Phase 1
   → Update Progress Tracking: Post-Design Constitution Check
8. Plan Phase 2 → Describe task generation approach (DO NOT create tasks.md)
9. STOP - Ready for /tasks command
```

**IMPORTANT**: The /plan command STOPS at step 7. Phases 2-4 are executed by other commands:
- Phase 2: /tasks command creates tasks.md
- Phase 3-4: Implementation execution (manual or via tools)

## Summary
Primary requirement: Enable users to read and list files from GitHub repositories through declarative YAML configurations using the `gh` CLI tool for API interactions. Technical approach: Config-driven architecture with UV workspace modular design, Pydantic validation-first processing, and thin CLI orchestration using `gh api` commands.

## Technical Context
**Language/Version**: Python 3.12+
**Primary Dependencies**: gh CLI tool (external), subprocess for CLI interaction, Pydantic for validation, Jinja2 for templating
**Storage**: YAML configuration files with optional variable files
**Testing**: pytest with contract tests, integration tests, and unit tests
**Target Platform**: Cross-platform CLI tool (Linux, macOS, Windows)
**Project Type**: single - modular CLI toolkit with UV workspaces
**Performance Goals**: Schema validation <100ms, gh CLI execution <5s, template rendering <50ms
**Constraints**: Config-driven (no hardcoded logic), validation-first (no gh CLI calls without schema pass), modular UV workspaces, gh CLI authentication required
**Scale/Scope**: MVP supports 2 operations (file read, directory listing), extensible for additional GitHub API operations

## Constitution Check
*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Based on Untaped Constitution principles:

**Core Principles Gates:**
- [x] Config-Driven Architecture: PASS - All workflow logic in YAML configs, Python only as execution engine
- [x] Validation-First Processing: PASS - Pydantic schema validation before any gh CLI calls
- [x] UV Workspace Modular Design: PASS - Repository organized as UV workspaces in packages/ directory (untaped-core, untaped-ansible, untaped-cli) + new untaped-github package
- [x] Extensible-by-Design: PASS - Configuration-driven approach enables extension through schema additions

**Infrastructure-as-Code Standards:**
- [x] YAML as primary config format with Jinja2 templating support: PASS - Design uses YAML with Jinja2 rendering
- [x] Pydantic schema validation as first step in every workflow: PASS - Validation occurs before gh CLI calls
- [x] UV commands for all workspace/package management operations: PASS - Using uv init, uv add, uv sync
- [x] Idempotent Ansible Tower API interactions with proper error handling: PASS - gh CLI interactions designed to be idempotent with comprehensive error handling

**Development Workflow Gates:**
- [x] All code passes schema validation tests before merge: PASS - Design includes comprehensive validation testing
- [x] Integration tests pass against test Ansible Tower instance: PASS - Design includes integration testing with gh CLI
- [x] 85%+ code coverage for all libraries: PASS - Design includes unit, integration, and contract tests
- [x] Static analysis and linting (black, flake8, mypy) pass: PASS - Design includes linting and type checking
- [x] Documentation updated for API/schema changes: PASS - Design includes documentation updates

**Governance:**
- [x] Semantic versioning (MAJOR.MINOR.PATCH) followed: PASS - Design includes semantic versioning
- [x] Breaking changes require MAJOR version bump and migration guide: PASS - Design includes migration planning
- [x] Complexity justified and documented: PASS - All design decisions documented with rationale

**Result**: PASS - All constitutional principles satisfied by design

## Project Structure

### Documentation (this feature)
```
specs/[###-feature]/
├── plan.md              # This file (/plan command output)
├── research.md          # Phase 0 output (/plan command)
├── data-model.md        # Phase 1 output (/plan command)
├── quickstart.md        # Phase 1 output (/plan command)
├── contracts/           # Phase 1 output (/plan command)
└── tasks.md             # Phase 2 output (/tasks command - NOT created by /plan)
```

### Source Code (repository root)
```
# Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure]
```

**Structure Decision**: Option 1 (Single project) - CLI toolkit with modular UV workspaces. Adding new `untaped-github` package for GitHub API operations.

## Phase 0: Outline & Research
1. **Extract unknowns from Technical Context** above:
   - For each NEEDS CLARIFICATION → research task
   - For each dependency → best practices task
   - For each integration → patterns task

2. **Generate and dispatch research agents**:
   ```
   For each unknown in Technical Context:
     Task: "Research {unknown} for {feature context}"
   For each technology choice:
     Task: "Find best practices for {tech} in {domain}"
   ```

3. **Consolidate findings** in `research.md` using format:
   - Decision: [what was chosen]
   - Rationale: [why chosen]
   - Alternatives considered: [what else evaluated]

**Output**: research.md with all NEEDS CLARIFICATION resolved

## Phase 1: Design & Contracts
*Prerequisites: research.md complete*

1. **Extract entities from feature spec** → `data-model.md`:
   - Entity name, fields, relationships
   - Validation rules from requirements
   - State transitions if applicable

2. **Generate API contracts** from functional requirements:
   - For each user action → endpoint
   - Use standard REST/GraphQL patterns
   - Output OpenAPI/GraphQL schema to `/contracts/`

3. **Generate contract tests** from contracts:
   - One test file per endpoint
   - Assert request/response schemas
   - Tests must fail (no implementation yet)

4. **Extract test scenarios** from user stories:
   - Each story → integration test scenario
   - Quickstart test = story validation steps

5. **Update agent file incrementally** (O(1) operation):
   - Run `.specify/scripts/bash/update-agent-context.sh cursor` for your AI assistant
   - If exists: Add only NEW tech from current plan
   - Preserve manual additions between markers
   - Update recent changes (keep last 3)
   - Keep under 150 lines for token efficiency
   - Output to repository root

**Output**: data-model.md, /contracts/*, failing tests, quickstart.md, agent-specific file

## Phase 2: Task Planning Approach
*This section describes what the /tasks command will do - DO NOT execute during /plan*

**Task Generation Strategy**:
- Load `.specify/templates/tasks-template.md` as base
- Generate tasks from Phase 1 design docs (contracts, data model, quickstart)
- Each contract → contract test task [P]
- Each entity → model creation task [P] 
- Each user story → integration test task
- Implementation tasks to make tests pass

**Ordering Strategy**:
- TDD order: Tests before implementation 
- Dependency order: Models before services before UI
- Mark [P] for parallel execution (independent files)

**Estimated Output**: 25-30 numbered, ordered tasks in tasks.md

**IMPORTANT**: This phase is executed by the /tasks command, NOT by /plan

## Phase 3+: Future Implementation
*These phases are beyond the scope of the /plan command*

**Phase 3**: Task execution (/tasks command creates tasks.md)  
**Phase 4**: Implementation (execute tasks.md following constitutional principles)  
**Phase 5**: Validation (run tests, execute quickstart.md, performance validation)

## Complexity Tracking
*Fill ONLY if Constitution Check has violations that must be justified*

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |


## Progress Tracking
*This checklist is updated during execution flow*

**Phase Status**:
- [x] Phase 0: Research complete (/plan command)
- [x] Phase 1: Design complete (/plan command)
- [x] Phase 2: Task planning complete (/tasks command)
- [x] Phase 3: Tasks generated (/tasks command)
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:
- [x] Initial Constitution Check: PASS
- [x] Post-Design Constitution Check: PASS
- [x] All NEEDS CLARIFICATION resolved
- [x] Complexity deviations documented - None required

---
*Based on Untaped Constitution v1.0.0 - See `/memory/constitution.md`*
