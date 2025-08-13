from typing import Any, Dict
import copy

class FilterModule(object):
    def filters(self):
        return {
            'dict_set': self.dict_set
        }

    def dict_set(self, d: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
        keys = path.split('.')
        current = copy.deepcopy(d)
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value
        return current
