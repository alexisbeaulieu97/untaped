# Ansible Tower API Contracts

**Date**: September 18, 2025  
**Feature**: MVP Infrastructure-as-Code Toolkit  
**Phase**: 1 (Design & Contracts)

## Authentication Contract

### Login Endpoint
```
POST /api/v2/authtoken/
Content-Type: application/json

Request:
{
  "username": "string",
  "password": "string"
}

Response (200):
{
  "token": "string",
  "expires": "2025-09-19T10:00:00Z"
}

Response (401):
{
  "detail": "Invalid username/password"
}
```

## Job Template Contracts

### List Job Templates
```
GET /api/v2/job_templates/
Authorization: Token {token}

Response (200):
{
  "count": 1,
  "results": [
    {
      "id": 42,
      "name": "my-job-template", 
      "description": "My job template",
      "job_type": "run",
      "inventory": 10,
      "project": 5,
      "playbook": "site.yml",
      "created": "2025-09-18T12:00:00Z",
      "modified": "2025-09-18T12:00:00Z"
    }
  ]
}
```

### Get Job Template by ID
```
GET /api/v2/job_templates/{id}/
Authorization: Token {token}

Response (200):
{
  "id": 42,
  "name": "my-job-template",
  "description": "My job template",
  "job_type": "run",
  "inventory": 10,
  "project": 5, 
  "playbook": "site.yml",
  "credentials": [15, 16],
  "forks": 5,
  "limit": "",
  "verbosity": 0,
  "extra_vars": "{}",
  "job_tags": "",
  "skip_tags": "",
  "start_at_task": "",
  "timeout": 0,
  "use_fact_cache": false,
  "host_config_key": "",
  "ask_scm_branch_on_launch": false,
  "ask_diff_mode_on_launch": false,
  "ask_variables_on_launch": false,
  "ask_limit_on_launch": false,
  "ask_tags_on_launch": false,
  "ask_skip_tags_on_launch": false,
  "ask_job_type_on_launch": false,
  "ask_verbosity_on_launch": false,
  "ask_inventory_on_launch": false,
  "ask_credential_on_launch": false,
  "survey_enabled": false,
  "become_enabled": false,
  "diff_mode": false,
  "allow_simultaneous": false,
  "created": "2025-09-18T12:00:00Z",
  "modified": "2025-09-18T12:00:00Z"
}

Response (404):
{
  "detail": "Not found."
}
```

### Create Job Template
```
POST /api/v2/job_templates/
Authorization: Token {token}
Content-Type: application/json

Request:
{
  "name": "my-job-template",
  "description": "My job template",
  "job_type": "run",
  "inventory": 10,
  "project": 5,
  "playbook": "site.yml",
  "credentials": [15],
  "forks": 5,
  "verbosity": 0,
  "extra_vars": "{\"env\": \"dev\"}",
  "timeout": 3600
}

Response (201):
{
  "id": 42,
  "name": "my-job-template",
  "description": "My job template",
  "job_type": "run",
  "inventory": 10,
  "project": 5,
  "playbook": "site.yml",
  "credentials": [15],
  "forks": 5,
  "verbosity": 0,
  "extra_vars": "{\"env\": \"dev\"}",
  "timeout": 3600,
  "created": "2025-09-18T12:00:00Z",
  "modified": "2025-09-18T12:00:00Z"
}

Response (400):
{
  "name": ["job template with this name already exists."],
  "inventory": ["Invalid pk \"999\" - object does not exist."]
}
```

### Update Job Template
```
PUT /api/v2/job_templates/{id}/
Authorization: Token {token}
Content-Type: application/json

Request:
{
  "name": "my-updated-job-template",
  "description": "Updated description",
  "job_type": "run",
  "inventory": 10,
  "project": 5,
  "playbook": "site.yml",
  "credentials": [15, 16],
  "forks": 10,
  "verbosity": 1,
  "extra_vars": "{\"env\": \"prod\"}",
  "timeout": 7200
}

Response (200):
{
  "id": 42,
  "name": "my-updated-job-template",
  "description": "Updated description", 
  "job_type": "run",
  "inventory": 10,
  "project": 5,
  "playbook": "site.yml",
  "credentials": [15, 16],
  "forks": 10,
  "verbosity": 1,
  "extra_vars": "{\"env\": \"prod\"}",
  "timeout": 7200,
  "modified": "2025-09-18T14:00:00Z"
}
```

### Delete Job Template
```
DELETE /api/v2/job_templates/{id}/
Authorization: Token {token}

Response (204): No Content

Response (404):
{
  "detail": "Not found."
}

Response (409):
{
  "detail": "Cannot delete job template being used by workflow templates."
}
```

## Workflow Job Template Contracts

### List Workflow Job Templates
```
GET /api/v2/workflow_job_templates/
Authorization: Token {token}

Response (200):
{
  "count": 1,
  "results": [
    {
      "id": 20,
      "name": "my-workflow",
      "description": "My workflow template",
      "extra_vars": "{}",
      "organization": 1,
      "survey_enabled": false,
      "allow_simultaneous": false,
      "created": "2025-09-18T12:00:00Z",
      "modified": "2025-09-18T12:00:00Z"
    }
  ]
}
```

### Get Workflow Job Template by ID
```
GET /api/v2/workflow_job_templates/{id}/
Authorization: Token {token}

Response (200):
{
  "id": 20,
  "name": "my-workflow",
  "description": "My workflow template",
  "extra_vars": "{\"env\": \"dev\"}",
  "organization": 1,
  "survey_enabled": false,
  "allow_simultaneous": false,
  "ask_variables_on_launch": false,
  "ask_inventory_on_launch": false,
  "ask_scm_branch_on_launch": false,
  "ask_limit_on_launch": false,
  "created": "2025-09-18T12:00:00Z",
  "modified": "2025-09-18T12:00:00Z"
}
```

### Create Workflow Job Template
```
POST /api/v2/workflow_job_templates/
Authorization: Token {token}
Content-Type: application/json

Request:
{
  "name": "my-workflow",
  "description": "My workflow template",
  "extra_vars": "{\"env\": \"dev\"}",
  "organization": 1,
  "allow_simultaneous": false
}

Response (201):
{
  "id": 20,
  "name": "my-workflow",
  "description": "My workflow template", 
  "extra_vars": "{\"env\": \"dev\"}",
  "organization": 1,
  "survey_enabled": false,
  "allow_simultaneous": false,
  "created": "2025-09-18T12:00:00Z",
  "modified": "2025-09-18T12:00:00Z"
}
```

### Update Workflow Job Template
```
PUT /api/v2/workflow_job_templates/{id}/
Authorization: Token {token}
Content-Type: application/json

Request:
{
  "name": "my-updated-workflow",
  "description": "Updated workflow description",
  "extra_vars": "{\"env\": \"prod\"}",
  "organization": 1,
  "allow_simultaneous": true
}

Response (200):
{
  "id": 20,
  "name": "my-updated-workflow",
  "description": "Updated workflow description",
  "extra_vars": "{\"env\": \"prod\"}",
  "organization": 1,
  "allow_simultaneous": true,
  "modified": "2025-09-18T14:00:00Z"
}
```

### Delete Workflow Job Template
```
DELETE /api/v2/workflow_job_templates/{id}/
Authorization: Token {token}

Response (204): No Content
```

## Workflow Job Template Node Contracts

### List Workflow Nodes
```
GET /api/v2/workflow_job_templates/{id}/workflow_job_template_nodes/
Authorization: Token {token}

Response (200):
{
  "count": 3,
  "results": [
    {
      "id": 100,
      "identifier": "start-job",
      "unified_job_template": 42,
      "success_nodes": [101],
      "failure_nodes": [102],
      "always_nodes": [],
      "credentials": [],
      "inventory": null,
      "extra_data": "{}",
      "created": "2025-09-18T12:00:00Z"
    }
  ]
}
```

### Create Workflow Node
```
POST /api/v2/workflow_job_templates/{workflow_id}/workflow_job_template_nodes/
Authorization: Token {token}
Content-Type: application/json

Request:
{
  "identifier": "start-job",
  "unified_job_template": 42,
  "extra_data": "{\"custom_var\": \"value\"}"
}

Response (201):
{
  "id": 100,
  "identifier": "start-job", 
  "unified_job_template": 42,
  "success_nodes": [],
  "failure_nodes": [],
  "always_nodes": [],
  "credentials": [],
  "inventory": null,
  "extra_data": "{\"custom_var\": \"value\"}",
  "created": "2025-09-18T12:00:00Z"
}
```

### Create Workflow Node Association
```
POST /api/v2/workflow_job_template_nodes/{parent_id}/success_nodes/
Authorization: Token {token}
Content-Type: application/json

Request:
{
  "id": 101
}

Response (204): No Content
```

## Resource Reference Contracts

### List Inventories
```
GET /api/v2/inventories/
Authorization: Token {token}

Response (200):
{
  "count": 2,
  "results": [
    {
      "id": 10,
      "name": "production-servers",
      "description": "Production server inventory",
      "organization": 1
    }
  ]
}
```

### List Projects
```  
GET /api/v2/projects/
Authorization: Token {token}

Response (200):
{
  "count": 2, 
  "results": [
    {
      "id": 5,
      "name": "my-app-project",
      "description": "My application project",
      "scm_type": "git",
      "scm_url": "https://github.com/example/repo.git",
      "organization": 1
    }
  ]
}
```

### List Credentials
```
GET /api/v2/credentials/
Authorization: Token {token}

Response (200):
{
  "count": 3,
  "results": [
    {
      "id": 15,
      "name": "production-ssh-key",
      "description": "SSH key for production servers",
      "credential_type": 1,
      "organization": 1
    }
  ]
}
```

## Error Response Contracts

### Authentication Errors
```json
// 401 Unauthorized
{
  "detail": "Invalid token."
}

// 403 Forbidden  
{
  "detail": "You do not have permission to perform this action."
}
```

### Validation Errors
```json
// 400 Bad Request
{
  "name": ["This field is required."],
  "inventory": ["Invalid pk \"999\" - object does not exist."],
  "extra_vars": ["Must be valid JSON."]
}
```

### Not Found Errors
```json
// 404 Not Found
{
  "detail": "Not found."
}
```

### Conflict Errors
```json
// 409 Conflict
{
  "detail": "Cannot delete job template being used by workflow templates.",
  "dependent_workflows": [
    {"id": 20, "name": "my-workflow"}
  ]
}
```

## Rate Limiting Contract
```
HTTP/1.1 429 Too Many Requests
Retry-After: 60

{
  "detail": "Request was throttled. Expected available in 60 seconds."
}
```

## API Version Contract
All endpoints require API version v2:
- Base URL: `{TOWER_HOST}/api/v2/`
- All responses include `"api_version": "v2"` field
- Unsupported versions return `400 Bad Request`