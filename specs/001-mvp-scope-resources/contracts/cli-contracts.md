# CLI Command Contracts

**Date**: September 18, 2025  
**Feature**: MVP Infrastructure-as-Code Toolkit  
**Phase**: 1 (Design & Contracts)

## CLI Interface Contracts

### Global Options
All commands support these common options:
- `--config-file PATH` - Path to YAML configuration file (required for create/update)
- `--vars-file PATH` - Path to variables file for template rendering
- `--var KEY=VALUE` - Individual variable for template rendering (repeatable)
- `--dry-run` - Validate and show changes without applying
- `--verbose` - Increase output verbosity
- `--help` - Show command help

### Common Exit Codes
- `0` - Success
- `1` - Configuration validation error
- `2` - Template rendering error  
- `3` - Ansible Tower API error
- `4` - Network connectivity error
- `5` - Authentication error
- `6` - Permission error
- `7` - Resource not found
- `8` - Resource already exists (create only)

## Create Command

### Interface
```bash
untaped create [OPTIONS] RESOURCE_TYPE
```

### Arguments
- `RESOURCE_TYPE` - Type of resource to create (job-template, workflow-job-template)

### Options
- `--config-file PATH` - YAML configuration file (required)
- `--vars-file PATH` - Variables file for templating
- `--var KEY=VALUE` - Template variable (repeatable)
- `--version VERSION` - Version suffix for resource name (e.g., "v2" → "job-test-v2")
- `--dry-run` - Validate without creating
- `--force` - Overwrite existing resource
- `--verbose` - Show detailed output

### Success Response
```json
{
  "status": "success",
  "action": "create",
  "resource_type": "job_template",
  "resource_name": "my-job-template",
  "resource_id": 42,
  "message": "Job template 'my-job-template' created successfully",
  "tower_url": "https://tower.example.com/#/templates/job_template/42"
}
```

### Error Response
```json
{
  "status": "error", 
  "error_code": "VALIDATION_FAILED",
  "message": "Configuration validation failed",
  "errors": [
    {
      "field": "job_template.inventory",
      "error": "Field required",
      "suggestion": "Add 'inventory: inventory-name' to your configuration"
    }
  ]
}
```

### Validation Rules
- Configuration file must exist and be valid YAML
- All template variables must be provided
- Resource name must be unique in Tower
- All referenced Tower resources (inventory, project, credentials) must exist

## Update Command

### Interface
```bash
untaped update [OPTIONS] RESOURCE_TYPE RESOURCE_NAME
```

### Arguments  
- `RESOURCE_TYPE` - Type of resource to update (job-template, workflow-job-template)
- `RESOURCE_NAME` - Name or ID of existing resource

### Options
- `--config-file PATH` - YAML configuration file (required)
- `--vars-file PATH` - Variables file for templating
- `--var KEY=VALUE` - Template variable (repeatable)  
- `--dry-run` - Show changes without applying
- `--verbose` - Show detailed output

### Success Response
```json
{
  "status": "success",
  "action": "update", 
  "resource_type": "job_template",
  "resource_name": "my-job-template",
  "resource_id": 42,
  "message": "Job template 'my-job-template' updated successfully",
  "changes": [
    {
      "field": "description",
      "old_value": "Old description",
      "new_value": "New description"
    }
  ],
  "tower_url": "https://tower.example.com/#/templates/job_template/42"
}
```

### Error Response
```json
{
  "status": "error",
  "error_code": "RESOURCE_NOT_FOUND", 
  "message": "Resource not found",
  "resource_type": "job_template",
  "resource_name": "nonexistent-template"
}
```

### Validation Rules
- Resource must exist in Tower
- Configuration file must be valid
- User must have update permissions for the resource
- Changes must pass schema validation

## Delete Command

### Interface
```bash  
untaped delete [OPTIONS] RESOURCE_TYPE RESOURCE_NAME
```

### Arguments
- `RESOURCE_TYPE` - Type of resource to delete (job-template, workflow-job-template)
- `RESOURCE_NAME` - Name or ID of resource to delete

### Options
- `--force` - Skip confirmation prompt
- `--dry-run` - Show what would be deleted without deleting
- `--verbose` - Show detailed output

### Success Response
```json
{
  "status": "success",
  "action": "delete",
  "resource_type": "job_template", 
  "resource_name": "my-job-template",
  "resource_id": 42,
  "message": "Job template 'my-job-template' deleted successfully"
}
```

### Error Response
```json
{
  "status": "error",
  "error_code": "RESOURCE_NOT_FOUND",
  "message": "Resource not found",
  "resource_type": "job_template",
  "resource_name": "nonexistent-template"
}
```

### Validation Rules
- Resource must exist in Tower
- User must have delete permissions
- Resource must not be in use by workflows (for job templates)

## Configuration File Schema

### Job Template Configuration
```yaml
resource_type: job_template
job_template:
  name: "{{ template_name | default('my-job-template') }}"
  description: "{{ description | default('') }}"
  job_type: run
  inventory: "{{ inventory_name }}"
  project: "{{ project_name }}"
  playbook: "{{ playbook_path }}"
  credentials:
    - "{{ credential_name }}"
  forks: 5
  verbosity: 0
  extra_vars:
    environment: "{{ env | default('dev') }}"
  timeout: 3600
```

### Workflow Job Template Configuration  
```yaml
resource_type: workflow_job_template
workflow_job_template:
  name: "{{ workflow_name }}"
  description: "{{ description | default('') }}"
  extra_vars:
    environment: "{{ env | default('dev') }}"
  workflow_nodes:
    - identifier: "start-job"
      unified_job_template: "{{ start_job_template }}"
      success_nodes:
        - "success-job"
      failure_nodes:
        - "failure-job"
    - identifier: "success-job"
      unified_job_template: "{{ success_job_template }}"
    - identifier: "failure-job" 
      unified_job_template: "{{ failure_job_template }}"
```

## Template Variable Contracts

### Variable File Format
```yaml
# variables.yml
template_name: "my-deployment-job"
description: "Deploy application to production"
inventory_name: "production-servers"
project_name: "my-app-project"
playbook_path: "deploy.yml"
credential_name: "production-ssh-key"
env: "prod"
```

### Command Line Variables
```bash
untaped create job-template \
  --config-file job-template.yml \
  --var template_name="my-job" \
  --var env="staging" \
  --var inventory_name="staging-servers"
```

## Authentication Contracts

### Environment Variables
- `TOWER_HOST` - Ansible Tower hostname (required)
- `TOWER_USERNAME` - Username for authentication (required)  
- `TOWER_PASSWORD` - Password for authentication (required)
- `TOWER_VERIFY_SSL` - Verify SSL certificates (default: true)

### Configuration File
```yaml
# ~/.untaped/config.yml
tower:
  host: "https://tower.example.com"
  username: "admin"
  password: "${TOWER_PASSWORD}"  # Environment variable reference
  verify_ssl: true
  timeout: 30
```

## Error Handling Contracts

### Validation Error Format
```json
{
  "field": "job_template.inventory",
  "error": "Field required",
  "error_code": "MISSING_FIELD",
  "suggestion": "Add 'inventory: inventory-name' to your configuration",
  "line_number": 5,
  "column_number": 3
}
```

### Template Error Format
```json
{
  "variable": "inventory_name", 
  "error": "Undefined variable",
  "error_code": "UNDEFINED_VARIABLE",
  "suggestion": "Define 'inventory_name' in variables file or use --var inventory_name=value",
  "template_line": 6
}
```

### API Error Format
```json
{
  "tower_error": {
    "status_code": 400,
    "message": "Bad Request", 
    "details": "Inventory 'nonexistent' not found"
  },
  "error_code": "TOWER_API_ERROR",
  "suggestion": "Verify that inventory 'nonexistent' exists in Ansible Tower"
}
```