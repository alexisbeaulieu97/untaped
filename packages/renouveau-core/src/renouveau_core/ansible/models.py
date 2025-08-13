"""Data models for Ansible content."""

from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class AnsiblePlaybook(BaseModel):
    """Represents an Ansible playbook."""
    
    name: str = Field(..., description="Playbook name")
    path: Path = Field(..., description="Path to playbook file")
    hosts: Optional[str] = Field(None, description="Target hosts pattern")
    vars: Dict[str, Any] = Field(default_factory=dict, description="Playbook variables")
    tasks_count: int = Field(0, description="Number of tasks")
    roles_used: List[str] = Field(default_factory=list, description="Roles used in playbook")
    tags: List[str] = Field(default_factory=list, description="Available tags")
    is_valid: bool = Field(True, description="Whether playbook syntax is valid")
    errors: List[str] = Field(default_factory=list, description="Syntax errors if any")


class AnsibleRole(BaseModel):
    """Represents an Ansible role."""
    
    name: str = Field(..., description="Role name")
    path: Path = Field(..., description="Path to role directory")
    description: Optional[str] = Field(None, description="Role description from meta/main.yml")
    author: Optional[str] = Field(None, description="Role author")
    version: Optional[str] = Field(None, description="Role version")
    dependencies: List[str] = Field(default_factory=list, description="Role dependencies")
    platforms: List[Dict[str, str]] = Field(default_factory=list, description="Supported platforms")
    galaxy_tags: List[str] = Field(default_factory=list, description="Galaxy tags")
    has_tasks: bool = Field(False, description="Whether role has tasks")
    has_handlers: bool = Field(False, description="Whether role has handlers")
    has_vars: bool = Field(False, description="Whether role has variables")
    has_defaults: bool = Field(False, description="Whether role has defaults")
    has_templates: bool = Field(False, description="Whether role has templates")
    has_files: bool = Field(False, description="Whether role has files")
    has_meta: bool = Field(False, description="Whether role has meta information")
    is_valid: bool = Field(True, description="Whether role structure is valid")
    errors: List[str] = Field(default_factory=list, description="Validation errors if any")


class AnsibleInventory(BaseModel):
    """Represents an Ansible inventory."""
    
    name: str = Field(..., description="Inventory name")
    path: Path = Field(..., description="Path to inventory file/directory")
    format: str = Field(..., description="Inventory format (ini, yaml, script)")
    hosts_count: int = Field(0, description="Number of hosts")
    groups_count: int = Field(0, description="Number of groups")
    is_dynamic: bool = Field(False, description="Whether inventory is dynamic")
    is_valid: bool = Field(True, description="Whether inventory is valid")
    errors: List[str] = Field(default_factory=list, description="Validation errors if any")


class AnsibleProject(BaseModel):
    """Represents an Ansible project."""
    
    name: str = Field(..., description="Project name")
    root_path: Path = Field(..., description="Project root directory")
    playbooks: List[AnsiblePlaybook] = Field(default_factory=list, description="Project playbooks")
    roles: List[AnsibleRole] = Field(default_factory=list, description="Project roles")
    inventories: List[AnsibleInventory] = Field(default_factory=list, description="Project inventories")
    ansible_cfg: Optional[Path] = Field(None, description="Path to ansible.cfg")
    requirements_yml: Optional[Path] = Field(None, description="Path to requirements.yml")
    group_vars_dir: Optional[Path] = Field(None, description="Path to group_vars directory")
    host_vars_dir: Optional[Path] = Field(None, description="Path to host_vars directory")
    collections_path: Optional[Path] = Field(None, description="Path to collections")
    is_valid: bool = Field(True, description="Whether project structure is valid")
    warnings: List[str] = Field(default_factory=list, description="Project warnings")
    
    class Config:
        """Pydantic configuration."""
        arbitrary_types_allowed = True
