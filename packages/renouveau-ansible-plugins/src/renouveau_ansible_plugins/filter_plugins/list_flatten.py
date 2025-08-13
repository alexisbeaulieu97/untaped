from typing import List, Any

def flatten(lst: List[Any]) -> List[Any]:
    result = []
    for item in lst:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result

class FilterModule(object):
    def filters(self):
        return {
            'list_flatten': flatten
        }
