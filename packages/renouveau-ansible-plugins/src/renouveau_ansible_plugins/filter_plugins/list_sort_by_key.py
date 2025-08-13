from typing import List, Dict, Any

class FilterModule(object):
    def filters(self):
        return {
            'list_sort_by_key': self.list_sort_by_key
        }

    def list_sort_by_key(self, lst: List[Dict[str, Any]], key: str, order: str = 'asc') -> List[Dict[str, Any]]:
        reverse = order.lower() == 'desc'
        return sorted(lst, key=lambda x: x.get(key, ''), reverse=reverse)
