"""Generic API client using schema definitions."""

import requests
from typing import Dict, Any, Optional
from urllib.parse import urljoin
from .loader import APISchema, APIEndpoint
from .exceptions import ValidationError, AuthenticationError, NotFoundError, ServerError


class APIClient:
    """Generic API client that uses schema definitions."""
    
    def __init__(self, base_url: str, schema: APISchema, auth_token: Optional[str] = None):
        """Initialize the API client.
        
        Args:
            base_url: Base URL for the API (e.g., 'https://tower.example.com')
            schema: APISchema object defining the API structure
            auth_token: Authentication token for API requests
        """
        self.base_url = base_url.rstrip('/')
        self.schema = schema
        self.session = requests.Session()
        
        if auth_token:
            self.session.headers.update({
                'Authorization': f'Bearer {auth_token}'
            })
        
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
    
    def call_endpoint(self, endpoint_name: str, **kwargs) -> Dict[str, Any]:
        """Call an API endpoint defined in the schema.
        
        Args:
            endpoint_name: Name of the endpoint to call
            **kwargs: Parameters to pass to the endpoint
            
        Returns:
            JSON response data
            
        Raises:
            ValidationError: If parameters are invalid
            AuthenticationError: If authentication fails
            NotFoundError: If resource is not found
            ServerError: If server returns an error
        """
        if endpoint_name not in self.schema.endpoints:
            raise ValidationError(f"Unknown endpoint: {endpoint_name}")
        
        endpoint = self.schema.endpoints[endpoint_name]
        
        # Validate and extract parameters
        path_params = self._extract_parameters(endpoint, 'path', kwargs)
        query_params = self._extract_parameters(endpoint, 'query', kwargs)
        body_data = kwargs.get('body', {})
        
        # Build URL
        url = self._build_url(endpoint, path_params)
        
        # Make request
        response = self.session.request(
            method=endpoint.method,
            url=url,
            params=query_params,
            json=body_data if endpoint.method in ['POST', 'PUT', 'PATCH'] else None
        )
        
        # Handle response
        return self._handle_response(response)
    
    def _extract_parameters(self, endpoint: APIEndpoint, param_type: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Extract and validate parameters of a specific type."""
        extracted = {}
        
        if param_type not in endpoint.parameters:
            return extracted
        
        for param_name, param_def in endpoint.parameters[param_type].items():
            if param_name in kwargs:
                value = kwargs[param_name]
                # Basic type validation could be added here
                extracted[param_name] = value
            elif param_def.required:
                raise ValidationError(f"Required parameter missing: {param_name}")
        
        return extracted
    
    def _build_url(self, endpoint: APIEndpoint, path_params: Dict[str, Any]) -> str:
        """Build the full URL for an endpoint."""
        # Replace path parameters in the endpoint path
        path = endpoint.path
        for param_name, param_value in path_params.items():
            path = path.replace(f'{{{param_name}}}', str(param_value))
        
        # Combine with base URL and schema base path
        full_path = self.schema.base_path.rstrip('/') + path
        return urljoin(self.base_url, full_path)
    
    def _handle_response(self, response: requests.Response) -> Dict[str, Any]:
        """Handle HTTP response and raise appropriate exceptions."""
        if response.status_code == 401:
            raise AuthenticationError("Authentication failed")
        elif response.status_code == 404:
            raise NotFoundError("Resource not found")
        elif response.status_code >= 500:
            raise ServerError(f"Server error: {response.status_code}")
        elif response.status_code >= 400:
            raise ServerError(f"Client error: {response.status_code}")
        
        response.raise_for_status()
        
        try:
            return response.json()
        except ValueError:
            return {'status': 'success', 'data': response.text}
