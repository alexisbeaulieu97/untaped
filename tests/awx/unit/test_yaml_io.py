from __future__ import annotations

from pathlib import Path

import pytest

from untaped.api import ConfigError
from untaped.capabilities.awx.domain import Metadata, Resource
from untaped.capabilities.awx.infrastructure.yaml_io import dump_resource, read_resource_files


def _resource(kind: str, name: str, **spec: object) -> Resource:
    return Resource(
        kind=kind,
        metadata=Metadata(name=name, organization="Default"),
        spec=dict(spec),
    )


def write_resource(path: Path, resource: Resource, *, header_comment: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_resource(resource, header_comment=header_comment), encoding="utf-8")


def write_resources(path: Path, resources: list[Resource]) -> None:
    path.write_text("---\n".join(dump_resource(r) for r in resources), encoding="utf-8")


def read_resources(path: Path) -> list[Resource]:
    return [resource for _source, resource in read_resource_files(path)]


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


def test_directory_walk_includes_yaml_extension(tmp_path: Path) -> None:
    write_resource(tmp_path / "a.yml", _resource("JobTemplate", "deploy"))
    write_resource(tmp_path / "b.yaml", _resource("Project", "playbooks"))
    names = [r.metadata.name for r in read_resources(tmp_path)]
    assert names == ["deploy", "playbooks"]


def test_writers_emit_readable_utf8(tmp_path: Path) -> None:
    out = tmp_path / "jt.yml"
    r = _resource("JobTemplate", "déploiement", description="café ✓")
    write_resource(out, r)
    write_resources(tmp_path / "all.yml", [r])
    assert "café ✓" in out.read_text(encoding="utf-8")
    [back] = list(read_resources(tmp_path / "all.yml"))
    assert back == r


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
