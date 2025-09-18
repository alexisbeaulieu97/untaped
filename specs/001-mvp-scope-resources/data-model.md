# Data Model: MVP Infrastructure-as-Code Toolkit

**Date**: September 18, 2025  
**Feature**: MVP Infrastructure-as-Code Toolkit  
**Phase**: 1 (Design & Contracts)

## Core Entities

### ConfigurationFile
**Purpose**: Represents a YAML configuration file containing resource definitions  
**Fields**:
- `path: str` - Absolute path to the configuration file
- `content: str` - Raw YAML content before template rendering
- `variables: Dict[str, Any]` - Template variables for Jinja2 rendering
- `rendered_content: str` - YAML content after template rendering
- `resource_type: ResourceType` - Type of resource defined (job_template, workflow_job_template)

**Validation Rules**:
- Path must exist and be readable
- Content must be valid YAML syntax
- Variables must match template placeholders
- Resource type must be supported

**State Transitions**:
1. Raw → Loaded (file read)
2. Loaded → Templated (Jinja2 rendering)
3. Templated → Validated (schema validation)
4. Validated → Ready (for API operations)

### JobTemplate
**Purpose**: Ansible Tower job template resource configuration  
**Fields**:
- `name: str` - Unique identifier for the job template
- `description: Optional[str]` - Human-readable description
- `job_type: JobType` - run, check, or scan
- `inventory: str` - Name or ID of inventory
- `project: str` - Name or ID of project
- `playbook: str` - Path to playbook within project
- `credentials: List[str]` - List of credential names/IDs
- `forks: Optional[int]` - Number of parallel processes (default: 5)
- `limit: Optional[str]` - Host pattern limit
- `verbosity: Optional[int]` - Ansible verbosity level (0-5)
- `extra_vars: Optional[Dict[str, Any]]` - Additional variables
- `job_tags: Optional[str]` - Ansible tags to run
- `skip_tags: Optional[str]` - Ansible tags to skip
- `start_at_task: Optional[str]` - Task name to start at
- `timeout: Optional[int]` - Job timeout in seconds
- `use_fact_cache: Optional[bool]` - Enable fact caching
- `host_config_key: Optional[str]` - Host configuration key
- `ask_scm_branch_on_launch: Optional[bool]` - Prompt for SCM branch
- `ask_diff_mode_on_launch: Optional[bool]` - Prompt for diff mode
- `ask_variables_on_launch: Optional[bool]` - Prompt for extra variables
- `ask_limit_on_launch: Optional[bool]` - Prompt for limit
- `ask_tags_on_launch: Optional[bool]` - Prompt for tags
- `ask_skip_tags_on_launch: Optional[bool]` - Prompt for skip tags
- `ask_job_type_on_launch: Optional[bool]` - Prompt for job type
- `ask_verbosity_on_launch: Optional[bool]` - Prompt for verbosity
- `ask_inventory_on_launch: Optional[bool]` - Prompt for inventory
- `ask_credential_on_launch: Optional[bool]` - Prompt for credentials

**Validation Rules**:
- Name must be unique within Tower instance
- Inventory and project must exist in Tower
- Playbook must exist in specified project
- Credentials must exist and be appropriate for job type
- Verbosity must be 0-5
- Timeout must be positive integer
- Job type must be valid enum value

**Relationships**:
- References Project (external Tower resource)
- References Inventory (external Tower resource)
- References multiple Credentials (external Tower resources)

### WorkflowJobTemplate
**Purpose**: Ansible Tower workflow job template resource configuration  
**Fields**:
- `name: str` - Unique identifier for the workflow
- `description: Optional[str]` - Human-readable description
- `extra_vars: Optional[Dict[str, Any]]` - Additional variables
- `organization: Optional[str]` - Organization name/ID
- `survey_enabled: Optional[bool]` - Enable survey
- `allow_simultaneous: Optional[bool]` - Allow concurrent executions
- `ask_variables_on_launch: Optional[bool]` - Prompt for extra variables
- `ask_inventory_on_launch: Optional[bool]` - Prompt for inventory
- `ask_scm_branch_on_launch: Optional[bool]` - Prompt for SCM branch
- `ask_limit_on_launch: Optional[bool]` - Prompt for limit
- `workflow_nodes: List[WorkflowNode]` - Workflow execution nodes

**Validation Rules**:
- Name must be unique within Tower instance
- Organization must exist if specified
- Workflow nodes must form valid DAG (no cycles)
- All referenced job templates must exist
- Node identifiers must be unique within workflow

**Relationships**:
- Contains multiple WorkflowNode entities
- References Organization (external Tower resource)

### WorkflowNode
**Purpose**: Individual node within a workflow job template  
**Fields**:
- `identifier: str` - Unique identifier within workflow
- `unified_job_template: str` - Name/ID of job template or workflow to execute
- `success_nodes: List[str]` - Node identifiers to execute on success
- `failure_nodes: List[str]` - Node identifiers to execute on failure
- `always_nodes: List[str]` - Node identifiers to always execute
- `credentials: Optional[List[str]]` - Override credentials for this node
- `diff_mode: Optional[bool]` - Enable diff mode for this node
- `extra_data: Optional[Dict[str, Any]]` - Extra variables for this node
- `inventory: Optional[str]` - Override inventory for this node
- `job_tags: Optional[str]` - Tags to run for this node
- `job_type: Optional[JobType]` - Override job type for this node
- `limit: Optional[str]` - Override host limit for this node
- `scm_branch: Optional[str]` - Override SCM branch for this node
- `skip_tags: Optional[str]` - Tags to skip for this node
- `verbosity: Optional[int]` - Override verbosity for this node

**Validation Rules**:
- Identifier must be unique within parent workflow
- Unified job template must exist in Tower
- Success/failure/always node references must be valid
- Node relationships must not create cycles
- Override values must pass same validation as JobTemplate fields

**Relationships**:
- Belongs to WorkflowJobTemplate
- References other WorkflowNode entities (success/failure/always chains)
- References JobTemplate or WorkflowJobTemplate (unified_job_template)

### VariableFile
**Purpose**: External YAML file containing template variables  
**Fields**:
- `path: str` - Absolute path to the variable file
- `variables: Dict[str, Any]` - Parsed variable content
- `environment: Optional[str]` - Environment label (dev, staging, prod)

**Validation Rules**:
- Path must exist and be readable
- Content must be valid YAML
- Variables must be JSON-serializable
- No circular references in nested structures

**Relationships**:
- Used by ConfigurationFile for template rendering

### ValidationResult
**Purpose**: Result of schema validation process  
**Fields**:
- `is_valid: bool` - Whether validation passed
- `errors: List[ValidationError]` - Detailed error information
- `warnings: List[ValidationWarning]` - Non-fatal issues

**ValidationError Fields**:
- `field_path: str` - JSONPath to the invalid field
- `message: str` - Human-readable error description
- `error_code: str` - Machine-readable error identifier
- `suggested_fix: Optional[str]` - Suggested correction

**ValidationWarning Fields**:
- `field_path: str` - JSONPath to the field with warning
- `message: str` - Human-readable warning description
- `recommendation: str` - Suggested improvement

## Enums

### ResourceType
- `job_template` - Ansible job template
- `workflow_job_template` - Ansible workflow job template

### JobType  
- `run` - Execute playbook
- `check` - Dry-run mode
- `scan` - Fact gathering only

## Schema Versioning

### Version Strategy
- Schema versions follow semantic versioning (MAJOR.MINOR.PATCH)
- Backward compatibility maintained within MAJOR versions
- Migration guides provided for MAJOR version changes
- Multiple schema versions supported during transition periods

### Current Schemas
- `v1.0.0` - Initial MVP schemas for JobTemplate and WorkflowJobTemplate
- Future versions will add new resource types and fields

## Data Flow

1. **Configuration Loading**: ConfigurationFile loads YAML from disk
2. **Template Rendering**: Jinja2 processes template variables in ConfigurationFile
3. **Schema Validation**: Pydantic validates rendered content against resource schemas
4. **Resource Creation**: Validated data creates JobTemplate or WorkflowJobTemplate instances
5. **API Translation**: Resource instances translate to Ansible Tower API format

## Error Handling

### Validation Errors
- Field-level validation with specific error messages
- JSONPath references for precise error location
- Suggested fixes where applicable
- Error aggregation for multiple issues

### Template Errors
- Undefined variable detection
- Template syntax validation
- Variable type checking
- Template inheritance validation

### Configuration Errors
- File not found or unreadable
- YAML syntax errors
- Missing required sections
- Invalid resource type specification