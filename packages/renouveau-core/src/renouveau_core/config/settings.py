"""Pydantic settings for renouveau configuration."""

from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TowerConfig(BaseModel):
    """Ansible Tower/AWX configuration."""
    
    url: Optional[str] = Field(None, description="Tower/AWX base URL")
    token: Optional[str] = Field(None, description="API token for authentication")
    username: Optional[str] = Field(None, description="Username for authentication")
    password: Optional[str] = Field(None, description="Password for authentication")
    verify_ssl: bool = Field(True, description="Verify SSL certificates")
    timeout: int = Field(30, description="Request timeout in seconds")


class GitHubConfig(BaseModel):
    """GitHub configuration."""
    
    token: Optional[str] = Field(None, description="GitHub API token")
    organization: Optional[str] = Field(None, description="Default GitHub organization")
    base_url: str = Field("https://api.github.com", description="GitHub API base URL")
    timeout: int = Field(30, description="Request timeout in seconds")


class AnsibleConfig(BaseModel):
    """Local Ansible project configuration."""
    
    project_root: Path = Field(Path("."), description="Ansible project root directory")
    playbook_dirs: List[str] = Field(
        default_factory=lambda: ["playbooks/", "site.yml"], 
        description="Directories to search for playbooks"
    )
    role_dirs: List[str] = Field(
        default_factory=lambda: ["roles/"], 
        description="Directories to search for roles"
    )
    inventory_dirs: List[str] = Field(
        default_factory=lambda: ["inventory/", "inventories/"], 
        description="Directories to search for inventories"
    )
    group_vars_dirs: List[str] = Field(
        default_factory=lambda: ["group_vars/"], 
        description="Directories containing group variables"
    )
    host_vars_dirs: List[str] = Field(
        default_factory=lambda: ["host_vars/"], 
        description="Directories containing host variables"
    )
    collections_path: Optional[Path] = Field(
        None, description="Path to Ansible collections"
    )
    ansible_config: Optional[Path] = Field(
        None, description="Path to ansible.cfg file"
    )


class RenouveauSettings(BaseSettings):
    """Main application settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="RENOUVEAU_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Global settings
    debug: bool = Field(False, description="Enable debug mode")
    log_level: str = Field("INFO", description="Logging level")
    config_dir: Path = Field(
        Path.home() / ".config" / "renouveau",
        description="Configuration directory"
    )
    
    # Component configurations
    tower: TowerConfig = Field(default_factory=TowerConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    ansible: AnsibleConfig = Field(default_factory=AnsibleConfig)
    
    def __init__(self, config_file: Optional[Path] = None, **kwargs):
        """Initialize settings with optional config file."""
        if config_file and config_file.exists():
            # Load from YAML/TOML config file if provided
            import yaml
            with open(config_file, 'r') as f:
                file_config = yaml.safe_load(f) or {}
            
            # Merge file config with kwargs
            merged_config = {**file_config, **kwargs}
            super().__init__(**merged_config)
        else:
            super().__init__(**kwargs)
    
    @classmethod
    def load_from_file(cls, config_file: Path) -> "RenouveauSettings":
        """Load settings from a configuration file."""
        return cls(config_file=config_file)
    
    def save_to_file(self, config_file: Path) -> None:
        """Save current settings to a configuration file."""
        config_file.parent.mkdir(parents=True, exist_ok=True)
        
        import yaml
        config_data = self.model_dump(exclude_unset=True)
        
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f, default_flow_style=False)
    
    def get_default_config_file(self) -> Path:
        """Get the default configuration file path."""
        return self.config_dir / "config.yaml"
    
    def ensure_config_dir(self) -> None:
        """Ensure the configuration directory exists."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
