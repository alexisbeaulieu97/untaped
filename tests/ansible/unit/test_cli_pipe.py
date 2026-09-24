"""Typed-pipe (`--format pipe`) envelope tests for the Ansible tool.

Each row-producing command must tag its `--format pipe` output with a
namespaced `kind` hint so downstream consumers can route records without
sniffing fields. The state is written in the legacy top-level location
of ``config.yml``, which is moved to ``state.yml`` on first read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from untaped.capabilities.ansible.cli import app
from untaped.testing import CliInvoker


@pytest.mark.parametrize(
    ("args", "kind", "field", "value"),
    [
        (["alias", "list"], "ansible.alias", "alias", "common"),
        (["source", "list"], "ansible.source", "name", "prod"),
        (["source", "get", "prod"], "ansible.source", "name", "prod"),
        (["source", "status"], "ansible.source_status", "source", "prod"),
    ],
)
def test_pipe_output_tags_envelope_with_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    kind: str,
    field: str,
    value: str,
) -> None:
    cfg = tmp_path / "config.yml"
    profile = {"ansible": {"index_path": str(tmp_path / "index.sqlite3")}}
    state = {"aliases": {"common": "acme/common"}, "sources": [{"name": "prod", "repos": ["a/b"]}]}
    cfg.write_text(yaml.safe_dump({"profiles": {"default": profile}, "ansible": state}))
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    result = CliInvoker().invoke(app, [*args, "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert (envelope["untaped"], envelope["kind"]) == ("1", kind)
    assert envelope["record"][field] == value
