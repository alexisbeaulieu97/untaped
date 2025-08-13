from typing import Any, Dict, Optional
import copy

class FilterModule(object):
    def filters(self):
        return {
            'dict_get': self.dict_get
        }

    def dict_get(self, d: Dict[str, Any], path: str, default: Optional[Any] = None) -> Any:
        keys = path.split('.')
        current = d
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current
