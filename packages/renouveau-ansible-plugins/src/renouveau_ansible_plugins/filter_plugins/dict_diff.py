from typing import Dict, Any
from deepdiff import DeepDiff

class FilterModule(object):
    def filters(self):
        return {
            'dict_diff': self.dict_diff
        }

    def dict_diff(self, old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
        return DeepDiff(old, new, ignore_order=True)
