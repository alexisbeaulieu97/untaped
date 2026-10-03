"""Content hashes the state records carry (``sha256:<hex>``)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def value_hash(value: Any) -> str:
    """Hash of a JSON-serialisable value, independent of key order and whitespace."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return content_hash(canonical.encode("utf-8"))
