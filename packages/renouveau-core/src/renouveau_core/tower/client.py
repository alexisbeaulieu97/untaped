"""Ansible Tower API client implementation."""

from pathlib import Path
from typing import Dict, Any, List, Optional, Union
import requests
from requests.auth import HTTPBasicAuth

from renouveau_schema import SchemaLoader, APIClient
from renouveau_schema.framework.exceptions import (
    AuthenticationError, 
    NotFoundError, 
    ServerError
)
from renouveau_schema.tower import SCHEMA_DIR

from ..config import TowerConfig
from .exceptions import (
    TowerError,
    TowerConnectionError,
    TowerAuthenticationError,
    TowerNotFoundError,
    TowerServerError,
)


class TowerClient:
    """Ansible Tower/AWX API client using schema-driven approach."""
    
    def __init__(self, config: TowerConfig):
        """Initialize the Tower client.
        
        Args:
            config: Tower configuration settings
        """
        self.config = config
        self.schema_loader = SchemaLoader(SCHEMA_DIR)
        self._clients: Dict[str, APIClient] = {}
        
        # Test connection on initialization
        self._test_connection()
    
    def _get_auth_token(self) -> Optional[str]:
        """Get authentication token from config or environment."""
        if self.config.token:
            return self.config.token
        
        # If no token but username/password provided, try to get one
        if self.config.username and self.config.password:
            return self._obtain_token()
        
        return None
    
    def _obtain_token(self) -> str:
        """Obtain an API token using username/password."""
        url = f"{self.config.url.rstrip('/')}/api/v2/tokens/"
        
        auth = HTTPBasicAuth(self.config.username, self.config.password)
        data = {
            "description": "renouveau CLI token",
            "application": None,
            "scope": "write"
        }
        
        try:
            response = requests.post(
                url, 
                json=data, 
                auth=auth,
                verify=self.config.verify_ssl,
                timeout=self.config.timeout
            )
            response.raise_for_status()
            return response.json()["token"]
        except requests.RequestException as e:
            raise TowerAuthenticationError(f"Failed to obtain token: {e}")
    
    def _test_connection(self) -> None:
        """Test connection to Tower API."""
        try:
            url = f"{self.config.url.rstrip('/')}/api/v2/ping/"
            response = requests.get(
                url,
                verify=self.config.verify_ssl,
                timeout=self.config.timeout
            )
            response.raise_for_status()
        except requests.ConnectionError as e:
            raise TowerConnectionError(f"Cannot connect to Tower: {e}")
        except requests.RequestException as e:
            raise TowerError(f"Tower connection test failed: {e}")
    
    def _get_client(self, schema_name: str) -> APIClient:
        """Get or create an API client for a specific schema.
        
        Args:
            schema_name: Name of the schema file (e.g., 'job_templates.yaml')
            
        Returns:
            APIClient configured for the schema
        """
        if schema_name not in self._clients:
            schema = self.schema_loader.load_schema(schema_name)
            token = self._get_auth_token()
            
            self._clients[schema_name] = APIClient(
                base_url=self.config.url,
                schema=schema,
                auth_token=token
            )
        
        return self._clients[schema_name]
    
    def _handle_api_error(self, e: Exception) -> None:
        """Convert API framework exceptions to Tower-specific exceptions."""
        if isinstance(e, AuthenticationError):
            raise TowerAuthenticationError(str(e))
        elif isinstance(e, NotFoundError):
            raise TowerNotFoundError(str(e))
        elif isinstance(e, ServerError):
            raise TowerServerError(str(e))
        else:
            raise TowerError(str(e))
    
    # Job Template methods
    def list_job_templates(
        self, 
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        search: Optional[str] = None,
        name: Optional[str] = None,
        project: Optional[int] = None
    ) -> Dict[str, Any]:
        """List job templates.
        
        Args:
            page: Page number for pagination
            page_size: Number of results per page
            search: Search filter
            name: Filter by job template name
            project: Filter by project ID
            
        Returns:
            API response with job templates list
        """
        try:
            client = self._get_client('job_templates.yaml')
            return client.call_endpoint(
                'list',
                page=page,
                page_size=page_size,
                search=search,
                name=name,
                project=project
            )
        except Exception as e:
            self._handle_api_error(e)
    
    def get_job_template(self, template_id: int) -> Dict[str, Any]:
        """Get a specific job template.
        
        Args:
            template_id: Job template ID
            
        Returns:
            Job template details
        """
        try:
            client = self._get_client('job_templates.yaml')
            return client.call_endpoint('get', id=template_id)
        except Exception as e:
            self._handle_api_error(e)
    
    def launch_job_template(
        self,
        template_id: int,
        extra_vars: Optional[Dict[str, Any]] = None,
        limit: Optional[str] = None,
        job_tags: Optional[str] = None,
        skip_tags: Optional[str] = None,
        inventory: Optional[int] = None,
        credential: Optional[int] = None
    ) -> Dict[str, Any]:
        """Launch a job template.
        
        Args:
            template_id: Job template ID
            extra_vars: Extra variables for the job
            limit: Inventory subset
            job_tags: Job tags to run
            skip_tags: Job tags to skip
            inventory: Inventory ID to use
            credential: Credential ID to use
            
        Returns:
            Job launch response
        """
        try:
            client = self._get_client('job_templates.yaml')
            body = {}
            
            if extra_vars:
                body['extra_vars'] = extra_vars
            if limit:
                body['limit'] = limit
            if job_tags:
                body['job_tags'] = job_tags
            if skip_tags:
                body['skip_tags'] = skip_tags
            if inventory:
                body['inventory'] = inventory
            if credential:
                body['credential'] = credential
            
            return client.call_endpoint('launch', id=template_id, body=body)
        except Exception as e:
            self._handle_api_error(e)
    
    # Inventory methods
    def list_inventories(
        self,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        search: Optional[str] = None,
        name: Optional[str] = None,
        organization: Optional[int] = None
    ) -> Dict[str, Any]:
        """List inventories.
        
        Args:
            page: Page number for pagination
            page_size: Number of results per page
            search: Search filter
            name: Filter by inventory name
            organization: Filter by organization ID
            
        Returns:
            API response with inventories list
        """
        try:
            client = self._get_client('inventories.yaml')
            return client.call_endpoint(
                'list',
                page=page,
                page_size=page_size,
                search=search,
                name=name,
                organization=organization
            )
        except Exception as e:
            self._handle_api_error(e)
    
    def get_inventory(self, inventory_id: int) -> Dict[str, Any]:
        """Get a specific inventory.
        
        Args:
            inventory_id: Inventory ID
            
        Returns:
            Inventory details
        """
        try:
            client = self._get_client('inventories.yaml')
            return client.call_endpoint('get', id=inventory_id)
        except Exception as e:
            self._handle_api_error(e)
    
    def list_inventory_hosts(
        self,
        inventory_id: int,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        search: Optional[str] = None,
        name: Optional[str] = None
    ) -> Dict[str, Any]:
        """List hosts in an inventory.
        
        Args:
            inventory_id: Inventory ID
            page: Page number for pagination
            page_size: Number of results per page
            search: Search filter
            name: Filter by host name
            
        Returns:
            API response with hosts list
        """
        try:
            client = self._get_client('inventories.yaml')
            return client.call_endpoint(
                'hosts',
                id=inventory_id,
                page=page,
                page_size=page_size,
                search=search,
                name=name
            )
        except Exception as e:
            self._handle_api_error(e)
    
    def list_inventory_groups(
        self,
        inventory_id: int,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        search: Optional[str] = None,
        name: Optional[str] = None
    ) -> Dict[str, Any]:
        """List groups in an inventory.
        
        Args:
            inventory_id: Inventory ID
            page: Page number for pagination
            page_size: Number of results per page
            search: Search filter
            name: Filter by group name
            
        Returns:
            API response with groups list
        """
        try:
            client = self._get_client('inventories.yaml')
            return client.call_endpoint(
                'groups',
                id=inventory_id,
                page=page,
                page_size=page_size,
                search=search,
                name=name
            )
        except Exception as e:
            self._handle_api_error(e)
    
    # Project methods
    def list_projects(
        self,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        search: Optional[str] = None,
        name: Optional[str] = None,
        organization: Optional[int] = None,
        scm_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """List projects.
        
        Args:
            page: Page number for pagination
            page_size: Number of results per page
            search: Search filter
            name: Filter by project name
            organization: Filter by organization ID
            scm_type: Filter by SCM type
            
        Returns:
            API response with projects list
        """
        try:
            client = self._get_client('projects.yaml')
            return client.call_endpoint(
                'list',
                page=page,
                page_size=page_size,
                search=search,
                name=name,
                organization=organization,
                scm_type=scm_type
            )
        except Exception as e:
            self._handle_api_error(e)
    
    def get_project(self, project_id: int) -> Dict[str, Any]:
        """Get a specific project.
        
        Args:
            project_id: Project ID
            
        Returns:
            Project details
        """
        try:
            client = self._get_client('projects.yaml')
            return client.call_endpoint('get', id=project_id)
        except Exception as e:
            self._handle_api_error(e)
    
    def update_project(self, project_id: int) -> Dict[str, Any]:
        """Update a project from SCM.
        
        Args:
            project_id: Project ID
            
        Returns:
            Project update response
        """
        try:
            client = self._get_client('projects.yaml')
            return client.call_endpoint('update', id=project_id)
        except Exception as e:
            self._handle_api_error(e)
    
    def list_project_playbooks(self, project_id: int) -> List[str]:
        """List playbooks in a project.
        
        Args:
            project_id: Project ID
            
        Returns:
            List of playbook names
        """
        try:
            client = self._get_client('projects.yaml')
            return client.call_endpoint('playbooks', id=project_id)
        except Exception as e:
            self._handle_api_error(e)
    
    # Workflow Job Template methods
    def list_workflow_job_templates(
        self,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        search: Optional[str] = None,
        name: Optional[str] = None,
        organization: Optional[int] = None
    ) -> Dict[str, Any]:
        """List workflow job templates.
        
        Args:
            page: Page number for pagination
            page_size: Number of results per page
            search: Search filter
            name: Filter by workflow name
            organization: Filter by organization ID
            
        Returns:
            API response with workflow job templates list
        """
        try:
            client = self._get_client('workflow_job_templates.yaml')
            return client.call_endpoint(
                'list',
                page=page,
                page_size=page_size,
                search=search,
                name=name,
                organization=organization
            )
        except Exception as e:
            self._handle_api_error(e)
    
    def get_workflow_job_template(self, workflow_id: int) -> Dict[str, Any]:
        """Get a specific workflow job template.
        
        Args:
            workflow_id: Workflow job template ID
            
        Returns:
            Workflow job template details
        """
        try:
            client = self._get_client('workflow_job_templates.yaml')
            return client.call_endpoint('get', id=workflow_id)
        except Exception as e:
            self._handle_api_error(e)
    
    def list_workflow_nodes(
        self,
        workflow_id: int,
        page: Optional[int] = None,
        page_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """List workflow nodes for a workflow job template.
        
        Args:
            workflow_id: Workflow job template ID
            page: Page number for pagination
            page_size: Number of results per page
            
        Returns:
            API response with workflow nodes list
        """
        try:
            client = self._get_client('workflow_job_templates.yaml')
            return client.call_endpoint(
                'workflow_nodes',
                id=workflow_id,
                page=page,
                page_size=page_size
            )
        except Exception as e:
            self._handle_api_error(e)
    
    def find_workflows_using_job_template(self, job_template_id: int) -> List[Dict[str, Any]]:
        """Find all workflows that use a specific job template.
        
        Args:
            job_template_id: Job template ID to search for
            
        Returns:
            List of workflow job templates that contain the job template
        """
        try:
            workflows_using_template = []
            
            # Get all workflow job templates
            workflows_response = self.list_workflow_job_templates(page_size=200)
            workflows = workflows_response.get('results', [])
            
            # Check each workflow for the job template
            for workflow in workflows:
                workflow_id = workflow['id']
                
                # Get workflow nodes
                nodes_response = self.list_workflow_nodes(workflow_id, page_size=200)
                nodes = nodes_response.get('results', [])
                
                # Check if any node uses our job template
                for node in nodes:
                    if node.get('unified_job_template') == job_template_id:
                        # Add workflow info with node details
                        workflow_info = {
                            'workflow': workflow,
                            'node': node
                        }
                        workflows_using_template.append(workflow_info)
                        break  # Found it in this workflow, move to next
            
            return workflows_using_template
        except Exception as e:
            self._handle_api_error(e)
