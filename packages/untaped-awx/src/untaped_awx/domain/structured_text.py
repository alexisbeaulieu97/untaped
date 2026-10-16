"""Structured text: a JSON or YAML mapping or list that AWX stores as a string.

Fields such as a template's ``extra_vars`` or an inventory's ``variables`` hold
text; comparing or inspecting them means reading that text as the mapping or
list it encodes, and leaving any other text as it is.
"""

from __future__ import annotations

import json
from typing import Any

import yaml


def parse_structured_text(value: Any) -> Any:
    """``value`` parsed as JSON, else YAML, when it encodes a mapping or list; else ``value``."""
    if not isinstance(value, str):
        return value
    for parser in (json.loads, yaml.safe_load):
        try:
            parsed = parser(value)
        except ValueError, yaml.YAMLError:
            continue
        if isinstance(parsed, dict | list):
            return parsed
    return value


__all__ = ["parse_structured_text"]
