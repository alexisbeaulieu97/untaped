from __future__ import annotations

from pathlib import Path

import pytest
from untaped_awx.domain import Metadata, Resource
from untaped_awx.infrastructure.yaml_io import (
    dump_resource,
    read_resources,
    write_resource,
    write_resources,
)
from untaped_core import ConfigError


def _resource(kind: str, name: str, **spec: object) -> Resource:
    return Resource(
        kind=kind,
        metadata=Metadata(name=name, organization="Default"),
        spec=dict(spec),
    )


def test_round_trip_single_doc(tmp_path: Path) -> None:
    out = tmp_path / "jt.yml"
    r = _resource("JobTemplate", "deploy", playbook="deploy.yml")
    write_resource(out, r)
    [back] = list(read_resources(out))
    assert back == r


def test_round_trip_multi_doc(tmp_path: Path) -> None:
    out = tmp_path / "all.yml"
    resources = [
        _resource("JobTemplate", "deploy", playbook="deploy.yml"),
        _resource("Project", "playbooks", scm_type="git"),
    ]
    write_resources(out, resources)
    back = list(read_resources(out))
    assert back == resources


def test_directory_walk(tmp_path: Path) -> None:
    (tmp_path / "a.yml").write_text("")  # empty file is OK
    write_resource(tmp_path / "b.yml", _resource("JobTemplate", "deploy"))
    write_resource(tmp_path / "sub" / "c.yml", _resource("Project", "playbooks"))
    kinds = sorted(r.kind for r in read_resources(tmp_path))
    assert kinds == ["JobTemplate", "Project"]


def test_header_comment_preserved_in_output(tmp_path: Path) -> None:
    out = tmp_path / "wf.yml"
    r = _resource("WorkflowJobTemplate", "pipeline")
    write_resource(out, r, header_comment="nodes not saved (v0 limitation)")
    text = out.read_text()
    assert text.startswith("# nodes not saved")
    # Round-trips fine despite the comment
    [back] = list(read_resources(out))
    assert back == r


def test_invalid_yaml_raises_config_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text("kind: [unclosed")
    with pytest.raises(ConfigError):
        list(read_resources(bad))


def test_doc_with_unknown_field_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "kind: JobTemplate\nmetadata: {name: x}\nstatus: live\n"  # extra top-level
    )
    with pytest.raises(ConfigError):
        list(read_resources(bad))


def test_dump_returns_string() -> None:
    r = _resource("JobTemplate", "deploy", playbook="deploy.yml")
    text = dump_resource(r)
    assert "kind: JobTemplate" in text
    assert "name: deploy" in text


def test_missing_file_raises() -> None:
    with pytest.raises(ConfigError):
        list(read_resources(Path("/no/such/path.yml")))


def test_empty_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        list(read_resources(tmp_path))
