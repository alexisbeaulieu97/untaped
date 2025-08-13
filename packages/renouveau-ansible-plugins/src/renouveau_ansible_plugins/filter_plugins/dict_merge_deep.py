from typing import Dict, Any
import copy

def deep_merge(target: Dict[str, Any], *sources: Dict[str, Any]) -> Dict[str, Any]:
    for source in sources:
        for key, value in source.items():
            if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                deep_merge(target[key], value)
            else:
                target[key] = value
    return target

class FilterModule(object):
    def filters(self):
        return {
            'dict_merge_deep': self.dict_merge_deep
        }

    def dict_merge_deep(self, *dicts: Dict[str, Any]) -> Dict[str, Any]:
        if not dicts:
            return {}
        target = copy.deepcopy(dicts[0])
        return deep_merge(target, *dicts[1:])
