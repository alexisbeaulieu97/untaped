from typing import Dict, Any, List, Optional
import re

class FilterModule(object):
    def filters(self):
        return {
            'dict_filter_keys': self.dict_filter_keys
        }

    def dict_filter_keys(self, d: Dict[str, Any], include_regex: Optional[str] = None, include_list: Optional[List[str]] = None) -> Dict[str, Any]:
        result = {}
        for key, value in d.items():
            if include_regex and re.match(include_regex, key):
                result[key] = value
            elif include_list and key in include_list:
                result[key] = value
        return result
