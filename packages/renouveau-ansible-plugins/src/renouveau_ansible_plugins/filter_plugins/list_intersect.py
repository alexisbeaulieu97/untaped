from typing import List, Any

class FilterModule(object):
    def filters(self):
        return {
            'list_intersect': self.list_intersect
        }

    def list_intersect(self, *lists: List[Any]) -> List[Any]:
        if not lists:
            return []
        result = set(lists[0])
        for lst in lists[1:]:
            result.intersection_update(lst)
        return list(result)
