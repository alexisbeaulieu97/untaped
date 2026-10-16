"""Reading and writing ``Resource`` documents as YAML files."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.sdk import ConfigError
from untaped_awx.domain import Metadata, Resource
from untaped_awx.infrastructure.yaml_io import (
    dump_resource,
    read_resource_documents,
    read_resource_files,
    read_resource_text,
    resource_paths,
)


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


def test_multiline_strings_dump_as_literal_blocks(tmp_path: Path) -> None:
    extra_vars = "---\nregion: eu\nhosts:\n  - a\n"
    r = _resource("JobTemplate", "deploy", extra_vars=extra_vars, description="one line")
    text = dump_resource(r)
    assert "extra_vars: |\n    ---\n    region: eu\n" in text
    assert "description: one line" in text
    out = tmp_path / "jt.yml"
    write_resource(out, r)
    [back] = read_resources(out)
    assert back.spec["extra_vars"] == extra_vars


@pytest.mark.parametrize(
    "value",
    [
        "trailing space \nx",
        "tab\there\nx",
        "cr\r\nx",
        "no newline at end\nx",
        "\n",
        "a\x85b\nc",
        "a\u2028b\nc",
    ],
)
def test_awkward_multiline_strings_round_trip(tmp_path: Path, value: str) -> None:
    r = _resource("JobTemplate", "deploy", extra_vars=value)
    out = tmp_path / "jt.yml"
    write_resource(out, r)
    [back] = read_resources(out)
    assert back.spec["extra_vars"] == value


def test_comment_lines_follow_fidelity_note(tmp_path: Path) -> None:
    r = _resource("JobTemplate", "deploy")
    text = dump_resource(r, header_comment="partial", comment="Owner: team\n\nTicket 42")
    assert text.startswith("# partial\n# Owner: team\n#\n# Ticket 42\nkind: JobTemplate\n")
    out = tmp_path / "jt.yml"
    out.write_text(text)
    [back] = read_resources(out)
    assert back == r


_DOC = "kind: Project\nmetadata: {name: %s, organization: Default}\nspec: {}\n"


def _positions(text: str) -> list[tuple[int, str]]:
    """Each document's index with its resource name, or ``error: <message>``."""
    return [
        (index, f"error: {doc}" if isinstance(doc, str) else doc.metadata.name)
        for index, doc in read_resource_documents(text, source="specs.yml")
    ]


def test_read_resource_documents_counts_every_document_from_one() -> None:
    text = "\n".join(["---", _DOC % "a", "---", "", "---", "# nothing", "---", _DOC % "d"])

    assert _positions(text) == [(1, "a"), (4, "d")]


def test_read_resource_documents_reports_a_bad_document_and_reads_on() -> None:
    text = "---\n".join(["- not a mapping\n", _DOC % "b", "kind: Project\nstatus: x\n", _DOC % "d"])

    [first, second, third, fourth] = _positions(text)

    assert first == (1, "error: specs.yml: each YAML doc must be a mapping")
    assert second == (2, "b")
    assert third[0] == 3 and third[1].startswith("error: specs.yml: ")
    assert fourth == (4, "d")


def test_read_resource_documents_stops_a_file_at_its_yaml_error() -> None:
    text = "---\n".join([_DOC % "a", "kind: [unclosed\n", _DOC % "c"])

    [first, second] = _positions(text)

    assert first == (1, "a")
    assert second[0] == 2 and second[1].startswith("error: invalid YAML in specs.yml: ")


def test_read_resource_text_raises_the_first_bad_document() -> None:
    text = "---\n".join([_DOC % "a", "- not a mapping\n", "kind: [unclosed\n"])

    with pytest.raises(ConfigError) as info:
        list(read_resource_text(text, source="specs.yml"))

    assert str(info.value) == "specs.yml: each YAML doc must be a mapping"
    assert info.value.category == "invalid"


def test_resource_paths_lists_a_directory_sorted(tmp_path: Path) -> None:
    for name in ("b.yaml", "a.yml", "sub/c.yml", "notes.txt"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")

    assert resource_paths(tmp_path) == [
        tmp_path / "a.yml",
        tmp_path / "b.yaml",
        tmp_path / "sub/c.yml",
    ]
    assert resource_paths(tmp_path / "a.yml") == [tmp_path / "a.yml"]
