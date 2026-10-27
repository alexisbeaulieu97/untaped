"""Typed-pipe (`--format pipe`) envelope tests for the Ansible tool.

Each row-producing command must tag its `--format pipe` output with a
namespaced `kind` hint so downstream consumers can route records without
sniffing fields.
"""

from __future__ import annotations

import json

import pytest

from ansible_test_support import run, write_state


@pytest.mark.parametrize(
    ("args", "kind", "field", "value"),
    [
        (["source-alias", "list"], "ansible.source_alias", "alias", "common"),
        (["source", "list"], "ansible.source", "name", "prod"),
        (["source", "get", "prod"], "ansible.source", "name", "prod"),
        (["source", "status"], "ansible.source_status", "source", "prod"),
    ],
)
def test_pipe_output_tags_envelope_with_kind(
    args: list[str],
    kind: str,
    field: str,
    value: str,
) -> None:
    write_state(
        {"aliases": {"common": "acme/common"}, "sources": [{"name": "prod", "repos": ["a/b"]}]}
    )

    result = run(*args, "--format", "pipe")

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert (envelope["untaped"], envelope["kind"]) == ("1", kind)
    assert envelope["record"][field] == value
