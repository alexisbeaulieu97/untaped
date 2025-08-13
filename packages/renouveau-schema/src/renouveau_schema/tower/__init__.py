"""Ansible Tower API schemas."""

from pathlib import Path

# Get the directory containing the YAML schema files
SCHEMA_DIR = Path(__file__).parent

# Available schema files
AVAILABLE_SCHEMAS = [
    "job_templates.yaml",
    "inventories.yaml", 
    "projects.yaml",
    "workflow_job_templates.yaml",
]

__all__ = ["SCHEMA_DIR", "AVAILABLE_SCHEMAS"]
