"""Ansible project discovery and analysis."""

import yaml
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from ..config import AnsibleConfig
from .models import AnsibleProject, AnsibleRole, AnsiblePlaybook, AnsibleInventory


class AnsibleProjectDiscovery:
    """Discovers and analyzes Ansible project structure."""
    
    def __init__(self, config: AnsibleConfig):
        """Initialize the discovery engine.
        
        Args:
            config: Ansible configuration
        """
        self.config = config
    
    def discover_project(self, project_path: Optional[Path] = None) -> AnsibleProject:
        """Discover an Ansible project at the given path.
        
        Args:
            project_path: Path to project root, defaults to config.project_root
            
        Returns:
            AnsibleProject with discovered content
        """
        if project_path is None:
            project_path = self.config.project_root
        
        project_path = Path(project_path).resolve()
        project_name = project_path.name
        
        # Discover project components
        playbooks = self._discover_playbooks(project_path)
        roles = self._discover_roles(project_path)
        inventories = self._discover_inventories(project_path)
        
        # Find configuration files
        ansible_cfg = self._find_ansible_cfg(project_path)
        requirements_yml = self._find_requirements_yml(project_path)
        group_vars_dir = self._find_group_vars_dir(project_path)
        host_vars_dir = self._find_host_vars_dir(project_path)
        collections_path = self._find_collections_path(project_path)
        
        # Validate project structure
        warnings = self._validate_project_structure(project_path)
        
        return AnsibleProject(
            name=project_name,
            root_path=project_path,
            playbooks=playbooks,
            roles=roles,
            inventories=inventories,
            ansible_cfg=ansible_cfg,
            requirements_yml=requirements_yml,
            group_vars_dir=group_vars_dir,
            host_vars_dir=host_vars_dir,
            collections_path=collections_path,
            warnings=warnings
        )
    
    def _discover_playbooks(self, project_path: Path) -> List[AnsiblePlaybook]:
        """Discover playbooks in the project."""
        playbooks = []
        
        for playbook_pattern in self.config.playbook_dirs:
            if playbook_pattern.endswith('.yml') or playbook_pattern.endswith('.yaml'):
                # Single playbook file
                playbook_path = project_path / playbook_pattern
                if playbook_path.exists():
                    playbook = self._analyze_playbook(playbook_path)
                    if playbook:
                        playbooks.append(playbook)
            else:
                # Directory containing playbooks
                playbook_dir = project_path / playbook_pattern
                if playbook_dir.exists() and playbook_dir.is_dir():
                    for playbook_file in playbook_dir.glob('*.yml'):
                        playbook = self._analyze_playbook(playbook_file)
                        if playbook:
                            playbooks.append(playbook)
                    for playbook_file in playbook_dir.glob('*.yaml'):
                        playbook = self._analyze_playbook(playbook_file)
                        if playbook:
                            playbooks.append(playbook)
        
        return playbooks
    
    def _analyze_playbook(self, playbook_path: Path) -> Optional[AnsiblePlaybook]:
        """Analyze a single playbook file."""
        try:
            with open(playbook_path, 'r') as f:
                content = yaml.safe_load(f)
            
            if not isinstance(content, list):
                return None
            
            # Extract playbook information
            hosts = None
            vars_dict = {}
            tasks_count = 0
            roles_used = []
            tags = set()
            
            for play in content:
                if not isinstance(play, dict):
                    continue
                
                # Get hosts pattern
                if 'hosts' in play and hosts is None:
                    hosts = play['hosts']
                
                # Extract variables
                if 'vars' in play:
                    vars_dict.update(play.get('vars', {}))
                
                # Count tasks
                if 'tasks' in play:
                    tasks_count += len(play.get('tasks', []))
                    
                    # Extract tags from tasks
                    for task in play.get('tasks', []):
                        if isinstance(task, dict) and 'tags' in task:
                            task_tags = task['tags']
                            if isinstance(task_tags, list):
                                tags.update(task_tags)
                            elif isinstance(task_tags, str):
                                tags.add(task_tags)
                
                # Extract roles
                if 'roles' in play:
                    for role in play.get('roles', []):
                        if isinstance(role, str):
                            roles_used.append(role)
                        elif isinstance(role, dict) and 'role' in role:
                            roles_used.append(role['role'])
            
            return AnsiblePlaybook(
                name=playbook_path.stem,
                path=playbook_path,
                hosts=hosts,
                vars=vars_dict,
                tasks_count=tasks_count,
                roles_used=roles_used,
                tags=list(tags),
                is_valid=True,
                errors=[]
            )
        
        except Exception as e:
            return AnsiblePlaybook(
                name=playbook_path.stem,
                path=playbook_path,
                is_valid=False,
                errors=[str(e)]
            )
    
    def _discover_roles(self, project_path: Path) -> List[AnsibleRole]:
        """Discover roles in the project."""
        roles = []
        
        for role_dir_pattern in self.config.role_dirs:
            role_dir = project_path / role_dir_pattern
            if role_dir.exists() and role_dir.is_dir():
                for role_path in role_dir.iterdir():
                    if role_path.is_dir():
                        role = self._analyze_role(role_path)
                        if role:
                            roles.append(role)
        
        return roles
    
    def _analyze_role(self, role_path: Path) -> Optional[AnsibleRole]:
        """Analyze a single role directory."""
        try:
            role_name = role_path.name
            
            # Check role structure
            has_tasks = (role_path / 'tasks' / 'main.yml').exists()
            has_handlers = (role_path / 'handlers' / 'main.yml').exists()
            has_vars = (role_path / 'vars' / 'main.yml').exists()
            has_defaults = (role_path / 'defaults' / 'main.yml').exists()
            has_templates = (role_path / 'templates').exists()
            has_files = (role_path / 'files').exists()
            has_meta = (role_path / 'meta' / 'main.yml').exists()
            
            # Parse meta information if available
            description = None
            author = None
            version = None
            dependencies = []
            platforms = []
            galaxy_tags = []
            
            if has_meta:
                try:
                    with open(role_path / 'meta' / 'main.yml', 'r') as f:
                        meta = yaml.safe_load(f)
                    
                    if isinstance(meta, dict):
                        galaxy_info = meta.get('galaxy_info', {})
                        description = galaxy_info.get('description')
                        author = galaxy_info.get('author')
                        version = galaxy_info.get('version')
                        platforms = galaxy_info.get('platforms', [])
                        galaxy_tags = galaxy_info.get('galaxy_tags', [])
                        
                        deps = meta.get('dependencies', [])
                        for dep in deps:
                            if isinstance(dep, str):
                                dependencies.append(dep)
                            elif isinstance(dep, dict) and 'role' in dep:
                                dependencies.append(dep['role'])
                except Exception:
                    pass  # Ignore meta parsing errors
            
            return AnsibleRole(
                name=role_name,
                path=role_path,
                description=description,
                author=author,
                version=version,
                dependencies=dependencies,
                platforms=platforms,
                galaxy_tags=galaxy_tags,
                has_tasks=has_tasks,
                has_handlers=has_handlers,
                has_vars=has_vars,
                has_defaults=has_defaults,
                has_templates=has_templates,
                has_files=has_files,
                has_meta=has_meta,
                is_valid=True,
                errors=[]
            )
        
        except Exception as e:
            return AnsibleRole(
                name=role_path.name,
                path=role_path,
                is_valid=False,
                errors=[str(e)]
            )
    
    def _discover_inventories(self, project_path: Path) -> List[AnsibleInventory]:
        """Discover inventories in the project."""
        inventories = []
        
        for inventory_pattern in self.config.inventory_dirs:
            inventory_path = project_path / inventory_pattern
            if inventory_path.exists():
                if inventory_path.is_file():
                    inventory = self._analyze_inventory_file(inventory_path)
                    if inventory:
                        inventories.append(inventory)
                elif inventory_path.is_dir():
                    for inv_file in inventory_path.glob('*'):
                        if inv_file.is_file() and not inv_file.name.startswith('.'):
                            inventory = self._analyze_inventory_file(inv_file)
                            if inventory:
                                inventories.append(inventory)
        
        return inventories
    
    def _analyze_inventory_file(self, inventory_path: Path) -> Optional[AnsibleInventory]:
        """Analyze a single inventory file."""
        try:
            # Determine inventory format
            inv_format = "ini"  # Default
            if inventory_path.suffix in ['.yml', '.yaml']:
                inv_format = "yaml"
            elif inventory_path.is_file() and inventory_path.stat().st_mode & 0o111:
                inv_format = "script"
            
            hosts_count = 0
            groups_count = 0
            is_dynamic = inv_format == "script"
            
            if not is_dynamic:
                try:
                    with open(inventory_path, 'r') as f:
                        content = f.read()
                    
                    if inv_format == "yaml":
                        data = yaml.safe_load(content)
                        if isinstance(data, dict):
                            # Count hosts and groups in YAML inventory
                            for key, value in data.items():
                                if key == 'all':
                                    continue
                                groups_count += 1
                                if isinstance(value, dict) and 'hosts' in value:
                                    hosts_count += len(value['hosts'])
                    else:
                        # Parse INI format
                        lines = content.split('\n')
                        current_group = None
                        for line in lines:
                            line = line.strip()
                            if line.startswith('[') and line.endswith(']'):
                                current_group = line[1:-1]
                                if not current_group.endswith(':children'):
                                    groups_count += 1
                            elif line and not line.startswith('#') and current_group:
                                if not current_group.endswith(':children'):
                                    hosts_count += 1
                except Exception:
                    pass  # Ignore parsing errors for counting
            
            return AnsibleInventory(
                name=inventory_path.stem,
                path=inventory_path,
                format=inv_format,
                hosts_count=hosts_count,
                groups_count=groups_count,
                is_dynamic=is_dynamic,
                is_valid=True,
                errors=[]
            )
        
        except Exception as e:
            return AnsibleInventory(
                name=inventory_path.stem,
                path=inventory_path,
                format="unknown",
                is_valid=False,
                errors=[str(e)]
            )
    
    def _find_ansible_cfg(self, project_path: Path) -> Optional[Path]:
        """Find ansible.cfg file."""
        cfg_path = project_path / 'ansible.cfg'
        return cfg_path if cfg_path.exists() else None
    
    def _find_requirements_yml(self, project_path: Path) -> Optional[Path]:
        """Find requirements.yml file."""
        for name in ['requirements.yml', 'requirements.yaml']:
            req_path = project_path / name
            if req_path.exists():
                return req_path
        return None
    
    def _find_group_vars_dir(self, project_path: Path) -> Optional[Path]:
        """Find group_vars directory."""
        for pattern in self.config.group_vars_dirs:
            gv_path = project_path / pattern
            if gv_path.exists() and gv_path.is_dir():
                return gv_path
        return None
    
    def _find_host_vars_dir(self, project_path: Path) -> Optional[Path]:
        """Find host_vars directory.""" 
        for pattern in self.config.host_vars_dirs:
            hv_path = project_path / pattern
            if hv_path.exists() and hv_path.is_dir():
                return hv_path
        return None
    
    def _find_collections_path(self, project_path: Path) -> Optional[Path]:
        """Find collections path."""
        if self.config.collections_path:
            return self.config.collections_path
        
        # Check common collection paths
        for path in ['collections', 'ansible_collections']:
            coll_path = project_path / path
            if coll_path.exists() and coll_path.is_dir():
                return coll_path
        
        return None
    
    def _validate_project_structure(self, project_path: Path) -> List[str]:
        """Validate project structure and return warnings."""
        warnings = []
        
        # Check for ansible.cfg
        if not (project_path / 'ansible.cfg').exists():
            warnings.append("No ansible.cfg found - consider adding one for project-specific configuration")
        
        # Check for requirements.yml
        has_requirements = any((project_path / name).exists() 
                             for name in ['requirements.yml', 'requirements.yaml'])
        if not has_requirements:
            warnings.append("No requirements.yml found - consider adding one to manage role/collection dependencies")
        
        # Check for README
        has_readme = any((project_path / name).exists() 
                        for name in ['README.md', 'README.rst', 'README.txt'])
        if not has_readme:
            warnings.append("No README file found - consider adding documentation")
        
        return warnings
