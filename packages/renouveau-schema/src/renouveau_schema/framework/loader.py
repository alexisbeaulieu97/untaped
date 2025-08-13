"""Schema loading functionality for API definitions."""

import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from .exceptions import SchemaError


@dataclass
class APIParameter:
    """Represents an API parameter definition."""
    name: str
    type: str
    required: bool = False
    description: Optional[str] = None
    enum: Optional[List[str]] = None
    nullable: bool = False


@dataclass
class APIEndpoint:
    """Represents an API endpoint definition."""
    name: str
    method: str
    path: str
    description: Optional[str] = None
    parameters: Dict[str, Dict[str, APIParameter]] = field(default_factory=dict)
    response_schema: Optional[Dict[str, Any]] = None


@dataclass 
class APISchema:
    """Represents a complete API schema definition."""
    name: str
    version: str
    base_path: str
    endpoints: Dict[str, APIEndpoint] = field(default_factory=dict)
    components: Dict[str, Any] = field(default_factory=dict)


class SchemaLoader:
    """Loads and parses YAML API schema definitions."""
    
    def __init__(self, schema_dir: Path):
        """Initialize the schema loader.
        
        Args:
            schema_dir: Directory containing schema YAML files
        """
        self.schema_dir = Path(schema_dir)
        if not self.schema_dir.exists():
            raise SchemaError(f"Schema directory does not exist: {schema_dir}")
    
    def load_schema(self, schema_file: str) -> APISchema:
        """Load a schema from a YAML file.
        
        Args:
            schema_file: Name of the schema file (e.g., 'job_templates.yaml')
            
        Returns:
            APISchema object
            
        Raises:
            SchemaError: If the schema file cannot be loaded or parsed
        """
        schema_path = self.schema_dir / schema_file
        
        if not schema_path.exists():
            raise SchemaError(f"Schema file does not exist: {schema_path}")
        
        try:
            with open(schema_path, 'r') as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise SchemaError(f"Failed to parse YAML schema: {e}")
        except Exception as e:
            raise SchemaError(f"Failed to load schema file: {e}")
        
        return self._parse_schema(data)
    
    def _parse_schema(self, data: Dict[str, Any]) -> APISchema:
        """Parse raw YAML data into an APISchema object."""
        schema = APISchema(
            name=data.get('name', ''),
            version=data.get('version', ''),
            base_path=data.get('base_path', ''),
            components=data.get('components', {})
        )
        
        # Parse endpoints
        endpoints_data = data.get('endpoints', {})
        for endpoint_name, endpoint_data in endpoints_data.items():
            endpoint = self._parse_endpoint(endpoint_name, endpoint_data)
            schema.endpoints[endpoint_name] = endpoint
        
        return schema
    
    def _parse_endpoint(self, name: str, data: Dict[str, Any]) -> APIEndpoint:
        """Parse endpoint data into an APIEndpoint object."""
        endpoint = APIEndpoint(
            name=name,
            method=data.get('method', 'GET'),
            path=data.get('path', ''),
            description=data.get('description'),
            response_schema=data.get('response')
        )
        
        # Parse parameters
        parameters_data = data.get('parameters', {})
        for param_type, params in parameters_data.items():
            if param_type not in endpoint.parameters:
                endpoint.parameters[param_type] = {}
            
            for param_name, param_data in params.items():
                param = self._parse_parameter(param_name, param_data)
                endpoint.parameters[param_type][param_name] = param
        
        return endpoint
    
    def _parse_parameter(self, name: str, data: Any) -> APIParameter:
        """Parse parameter data into an APIParameter object."""
        # Handle case where parameter is just a type string
        if isinstance(data, str):
            return APIParameter(
                name=name,
                type=data,
                required=False,
                description=None,
                enum=None,
                nullable=False
            )
        
        # Handle case where parameter is a dictionary
        if isinstance(data, dict):
            return APIParameter(
                name=name,
                type=data.get('type', 'string'),
                required=data.get('required', False),
                description=data.get('description'),
                enum=data.get('enum'),
                nullable=data.get('nullable', False)
            )
        
        # Fallback for other types
        return APIParameter(
            name=name,
            type='string',
            required=False,
            description=str(data),
            enum=None,
            nullable=False
        )
    
    def list_schemas(self) -> List[str]:
        """List all available schema files in the schema directory."""
        return [f.name for f in self.schema_dir.glob('*.yaml')]
