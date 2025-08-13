from typing import List, Any, Optional

class FilterModule(object):
    def filters(self):
        return {
            'list_unique': self.list_unique
        }

    def list_unique(self, lst: List[Any], by_key: Optional[str] = None) -> List[Any]:
        if by_key:
            seen = set()
            result = []
            for item in lst:
                val = item.get(by_key) if isinstance(item, dict) else item
                if val not in seen:
                    seen.add(val)
                    result.append(item)
            return result
        else:
            return list(set(lst))
