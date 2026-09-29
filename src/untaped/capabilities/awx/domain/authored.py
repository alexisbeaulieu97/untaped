"""Document kinds people and agents write by hand, and their JSON Schemas.

``untaped awx schema KIND`` prints the schema of any kind registered in
:data:`AUTHORED_KINDS`; adding a kind is one entry there.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from untaped.capabilities.awx.domain.suite import Suite

AUTHORED_KINDS: dict[str, type[BaseModel]] = {
    "AwxTestSuite": Suite,
}
"""Document ``kind`` → the model that validates it."""

_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def authored_schema(kind: str) -> dict[str, Any]:
    """The JSON Schema of ``kind``'s document, with its keys as written in a file."""
    return {"$schema": _DIALECT, **AUTHORED_KINDS[kind].model_json_schema(by_alias=True)}
