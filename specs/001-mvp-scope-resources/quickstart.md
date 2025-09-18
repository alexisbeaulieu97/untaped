# Quickstart Guide: MVP Infrastructure-as-Code Toolkit

**Date**: September 18, 2025  
**Feature**: MVP Infrastructure-as-Code Toolkit  
**Phase**: 1 (Design & Contracts)

## Prerequisites

### System Requirements
- Python 3.11 or higher
- Access to Ansible Tower/AWX instance
- Network connectivity to Tower API

### Installation
```bash
# Install using pipx (recommended)
pipx install untaped

# Or install using uv
uv tool install untaped

# Or install in development mode
git clone https://github.com/example/untaped.git
cd untaped  
uv sync
uv run untaped --help
```

### Authentication Setup
```bash
# Set environment variables
export TOWER_HOST="https://tower.example.com"
export TOWER_USERNAME="admin"
export TOWER_PASSWORD="your-password"

# Or create configuration file
mkdir -p ~/.untaped
cat > ~/.untaped/config.yml << EOF
tower:
  host: "https://tower.example.com"
  username: "admin"
  password: "${TOWER_PASSWORD}"
  verify_ssl: true
EOF
```

## Quick Start Scenarios

### Scenario 1: Create a Simple Job Template

**Step 1**: Create configuration file
```bash
cat > simple-job-template.yml << EOF
resource_type: job_template
job_template:
  name: "my-first-job"
  description: "My first job template created with untaped"
  job_type: run
  inventory: "Demo Inventory"
  project: "Demo Project" 
  playbook: "hello_world.yml"
  credentials:
    - "Demo Credential"
  forks: 5
  verbosity: 0
  timeout: 3600
EOF
```

**Step 2**: Validate configuration
```bash
untaped create job-template --config-file simple-job-template.yml --dry-run
```

**Expected output**:
```json
{
  "status": "success",
  "action": "validate", 
  "message": "Configuration is valid",
  "would_create": {
    "resource_type": "job_template",
    "name": "my-first-job",
    "changes": "Would create new job template"
  }
}
```

**Step 3**: Create the job template
```bash
untaped create job-template --config-file simple-job-template.yml
```

**Expected output**:
```json
{
  "status": "success",
  "action": "create",
  "resource_type": "job_template", 
  "resource_name": "my-first-job",
  "resource_id": 42,
  "message": "Job template 'my-first-job' created successfully",
  "tower_url": "https://tower.example.com/#/templates/job_template/42"
}
```

### Scenario 2: Create Job Template with Templating

**Step 1**: Create template configuration
```bash
cat > templated-job.yml << EOF
resource_type: job_template
job_template:
  name: "{{ job_name }}-{{ environment }}"
  description: "{{ description | default('Deployment job for ' + environment) }}"
  job_type: run
  inventory: "{{ environment }}-servers"
  project: "{{ project_name }}"
  playbook: "deploy.yml"
  credentials:
    - "{{ environment }}-ssh-key"
  extra_vars:
    environment: "{{ environment }}"
    app_version: "{{ app_version | default('latest') }}"
  forks: "{{ forks | default(5) }}"
  timeout: 3600
EOF
```

**Step 2**: Create variables file
```bash
cat > staging-vars.yml << EOF
job_name: "deploy-myapp"
environment: "staging"
project_name: "MyApp Project"
app_version: "v1.2.3"
forks: 10
description: "Deploy MyApp to staging environment"
EOF
```

**Step 3**: Create with variables
```bash
untaped create job-template \
  --config-file templated-job.yml \
  --vars-file staging-vars.yml
```

**Expected output**:
```json
{
  "status": "success",
  "action": "create",
  "resource_type": "job_template",
  "resource_name": "deploy-myapp-staging", 
  "resource_id": 43,
  "message": "Job template 'deploy-myapp-staging' created successfully"
}
```

### Scenario 3: Create Workflow Job Template

**Step 1**: Create workflow configuration
```bash
cat > deployment-workflow.yml << EOF
resource_type: workflow_job_template
workflow_job_template:
  name: "{{ workflow_name }}"
  description: "Complete deployment workflow"
  extra_vars:
    environment: "{{ environment }}"
    notification_channel: "#deployments"
  workflow_nodes:
    - identifier: "pre-deploy-checks"
      unified_job_template: "pre-deploy-validation"
      success_nodes:
        - "deploy-application"
      failure_nodes:
        - "notify-failure"
        
    - identifier: "deploy-application" 
      unified_job_template: "{{ deploy_job_template }}"
      extra_data:
        app_version: "{{ app_version }}"
      success_nodes:
        - "post-deploy-tests"
      failure_nodes:
        - "rollback-deployment"
        
    - identifier: "post-deploy-tests"
      unified_job_template: "smoke-tests"
      success_nodes:
        - "notify-success"
      failure_nodes:
        - "rollback-deployment"
        
    - identifier: "rollback-deployment"
      unified_job_template: "rollback-job"
      always_nodes:
        - "notify-failure"
        
    - identifier: "notify-success"
      unified_job_template: "success-notification"
      
    - identifier: "notify-failure"
      unified_job_template: "failure-notification"
EOF
```

**Step 2**: Create with command-line variables
```bash
untaped create workflow-job-template \
  --config-file deployment-workflow.yml \
  --var workflow_name="production-deployment" \
  --var environment="production" \
  --var deploy_job_template="deploy-myapp-production" \
  --var app_version="v1.2.3"
```

### Scenario 4: Update Existing Resource

**Step 1**: Update configuration file
```bash
# Modify simple-job-template.yml
cat > simple-job-template.yml << EOF
resource_type: job_template
job_template:
  name: "my-first-job"
  description: "Updated description for my first job template"
  job_type: run
  inventory: "Demo Inventory"
  project: "Demo Project"
  playbook: "hello_world.yml" 
  credentials:
    - "Demo Credential"
  forks: 10  # Increased from 5
  verbosity: 1  # Increased from 0
  timeout: 7200  # Increased from 3600
EOF
```

**Step 2**: Preview changes
```bash
untaped update job-template my-first-job \
  --config-file simple-job-template.yml \
  --dry-run
```

**Expected output**:
```json
{
  "status": "success",
  "action": "validate",
  "message": "Configuration is valid",
  "would_update": {
    "resource_type": "job_template",
    "resource_name": "my-first-job",
    "changes": [
      {
        "field": "description",
        "old_value": "My first job template created with untaped",
        "new_value": "Updated description for my first job template"
      },
      {
        "field": "forks", 
        "old_value": 5,
        "new_value": 10
      },
      {
        "field": "verbosity",
        "old_value": 0,
        "new_value": 1
      },
      {
        "field": "timeout",
        "old_value": 3600,
        "new_value": 7200
      }
    ]
  }
}
```

**Step 3**: Apply changes
```bash
untaped update job-template my-first-job \
  --config-file simple-job-template.yml
```

### Scenario 5: Delete Resource

**Step 1**: Delete with confirmation
```bash
untaped delete job-template my-first-job
```

**Expected interaction**:
```
Are you sure you want to delete job template 'my-first-job' (ID: 42)? [y/N]: y
```

**Expected output**:
```json
{
  "status": "success",
  "action": "delete",
  "resource_type": "job_template",
  "resource_name": "my-first-job", 
  "resource_id": 42,
  "message": "Job template 'my-first-job' deleted successfully"
}
```

**Step 2**: Delete without confirmation
```bash
untaped delete job-template my-first-job --force
```

## Validation Examples

### Schema Validation Error
```bash
# Create invalid configuration
cat > invalid-job.yml << EOF
resource_type: job_template
job_template:
  name: ""  # Empty name
  # Missing required fields
  job_type: "invalid_type"
  verbosity: 10  # Out of range
EOF

untaped create job-template --config-file invalid-job.yml
```

**Expected output**:
```json
{
  "status": "error",
  "error_code": "VALIDATION_FAILED", 
  "message": "Configuration validation failed",
  "errors": [
    {
      "field": "job_template.name",
      "error": "String should have at least 1 character",
      "suggestion": "Provide a non-empty name for the job template"
    },
    {
      "field": "job_template.inventory",
      "error": "Field required", 
      "suggestion": "Add 'inventory: inventory-name' to your configuration"
    },
    {
      "field": "job_template.project",
      "error": "Field required",
      "suggestion": "Add 'project: project-name' to your configuration"
    },
    {
      "field": "job_template.playbook",
      "error": "Field required",
      "suggestion": "Add 'playbook: playbook.yml' to your configuration"
    },
    {
      "field": "job_template.job_type",
      "error": "Input should be 'run', 'check' or 'scan'",
      "suggestion": "Use one of: run, check, scan"
    },
    {
      "field": "job_template.verbosity", 
      "error": "Input should be less than or equal to 5",
      "suggestion": "Use verbosity level 0-5"
    }
  ]
}
```

### Template Variable Error
```bash
# Create template with missing variable
cat > missing-var.yml << EOF
resource_type: job_template
job_template:
  name: "{{ undefined_variable }}"
  description: "Test template"
  job_type: run
  inventory: "Demo Inventory"
  project: "Demo Project"
  playbook: "test.yml"
  credentials:
    - "Demo Credential"
EOF

untaped create job-template --config-file missing-var.yml
```

**Expected output**:
```json
{
  "status": "error",
  "error_code": "TEMPLATE_ERROR",
  "message": "Template rendering failed",
  "errors": [
    {
      "variable": "undefined_variable",
      "error": "Undefined variable", 
      "suggestion": "Define 'undefined_variable' in variables file or use --var undefined_variable=value",
      "template_line": 4
    }
  ]
}
```

## Troubleshooting

### Common Issues

**Issue**: `TOWER_HOST` not set
```bash
untaped create job-template --config-file job.yml
```
```json
{
  "status": "error",
  "error_code": "CONFIGURATION_ERROR",
  "message": "Tower connection not configured",
  "suggestion": "Set TOWER_HOST environment variable or create ~/.untaped/config.yml"
}
```

**Solution**:
```bash
export TOWER_HOST="https://your-tower.example.com"
```

**Issue**: Authentication failure
```json
{
  "status": "error", 
  "error_code": "AUTHENTICATION_ERROR",
  "message": "Authentication failed",
  "suggestion": "Verify TOWER_USERNAME and TOWER_PASSWORD are correct"
}
```

**Issue**: Resource not found
```json
{
  "status": "error",
  "error_code": "RESOURCE_NOT_FOUND",
  "message": "Inventory 'nonexistent-inventory' not found",
  "suggestion": "Verify the inventory exists in Ansible Tower or check the name spelling"
}
```

### Debug Mode
```bash
# Enable verbose output for debugging
untaped create job-template --config-file job.yml --verbose
```

**Debug output includes**:
- Template rendering details
- API request/response bodies
- Detailed error traces
- Performance timing information

### Dry-Run Best Practice
Always use `--dry-run` first to validate configurations:
```bash
# Validate before creating
untaped create job-template --config-file job.yml --dry-run

# Check what would change before updating
untaped update job-template my-job --config-file job.yml --dry-run

# See what would be deleted
untaped delete job-template my-job --dry-run
```

## Next Steps

1. **Version Management**: Use `--version` flag for resource versioning
2. **Multiple Environments**: Create separate variable files for dev/staging/prod
3. **CI/CD Integration**: Integrate untaped commands into deployment pipelines
4. **Configuration Management**: Store configurations in version control
5. **Advanced Templating**: Explore Jinja2 filters and macros for complex configurations