from typing import List, Any

class FilterModule(object):
    def filters(self):
        return {
            'list_diff': self.list_diff
        }

    def list_diff(self, list1: List[Any], list2: List[Any]) -> List[Any]:
        return list(set(list1) - set(list2))
