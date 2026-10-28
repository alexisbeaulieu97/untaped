"""``untaped awx schema KIND``: the JSON Schema of an authored document kind."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import yaml

from awx_test_support import run


def _properties(schema: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    yield from schema.get("properties", {}).items()
    for definition in schema.get("$defs", {}).values():
        yield from definition.get("properties", {}).items()


def test_schema_prints_the_suite_document_as_json_by_default() -> None:
    result = run(["schema", "AwxTestSuite"])

    assert result.exit_code == 0, result.output
    schema = json.loads(result.stdout)
    assert schema["title"] == "AwxTestSuite"
    assert schema["additionalProperties"] is False
    assert {
        "kind",
        "name",
        "jobTemplate",
        "workflowTemplate",
        "organization",
        "defaults",
        "cases",
    } <= set(schema["properties"])
    # ``name`` defaults to the file name; the loader requires ``kind``.
    assert set(schema["required"]) == {"kind", "cases"}
    # A suite launches a job template or a workflow, never both.
    assert schema["oneOf"] == [{"required": ["jobTemplate"]}, {"required": ["workflowTemplate"]}]
    # Header variables are filled by the loader, never read from the body.
    assert schema["properties"]["variables"]["readOnly"] is True


def test_the_schema_describes_workflow_cases() -> None:
    schema = json.loads(run(["schema", "AwxTestSuite"]).stdout)
    definitions = schema["$defs"]

    assert definitions["Case"]["properties"]["approvals"]["anyOf"][0]["enum"] == [
        "approve",
        "deny",
    ]
    nodes = definitions["Expectation"]["properties"]["nodes"]
    assert nodes["additionalProperties"] == {"$ref": "#/$defs/NodeExpectation"}
    node = definitions["NodeExpectation"]
    assert "never_ran" in node["properties"]["status"]["anyOf"][0]["enum"]
    assert {"idempotent", "nodes"}.isdisjoint(node["properties"])


def test_every_schema_property_is_described() -> None:
    schema = json.loads(run(["schema", "AwxTestSuite"]).stdout)

    undescribed = [name for name, body in _properties(schema) if not body.get("description")]

    assert undescribed == []


def test_schema_prints_yaml_on_request() -> None:
    as_json = json.loads(run(["schema", "AwxTestSuite"]).stdout)

    result = run(["schema", "AwxTestSuite", "--yaml"])

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.stdout) == as_json


def test_schema_of_an_unknown_kind_is_a_usage_error() -> None:
    result = run(["schema", "Nope"])

    assert result.exit_code == 2
    assert "Nope" in result.stderr
    assert "AwxTestSuite" in result.stderr


def test_schema_help_lists_the_kinds() -> None:
    result = run(["schema", "--help"])

    assert result.exit_code == 0
    assert "AwxTestSuite" in result.stdout
