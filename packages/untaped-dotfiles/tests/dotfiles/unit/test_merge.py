"""The deep merge, its managed key paths, and the document formats."""

from __future__ import annotations

from pathlib import Path

from untaped_dotfiles.domain.hashing import content_hash, value_hash
from untaped_dotfiles.domain.merge import leaf_paths, merge_into, project, remove_paths
from untaped_dotfiles.infrastructure import FilesystemPlacer
from untaped_dotfiles.infrastructure.documents import dump_document, load_document


def test_mappings_recurse_and_scalars_and_lists_replace() -> None:
    target = {"a": {"x": 1, "y": [1, 2]}, "keep": True, "list": [1]}
    merge_into(target, {"a": {"y": [3], "z": {"n": 1}}, "list": [9, 8], "new": "v"})
    assert target == {
        "a": {"x": 1, "y": [3], "z": {"n": 1}},
        "keep": True,
        "list": [9, 8],
        "new": "v",
    }


def test_leaf_paths_name_every_value_written() -> None:
    source = {"a": {"y": [3], "z": {"n": 1}, "empty": {}}, "new": "v"}
    assert list(leaf_paths(source)) == [("a", "y"), ("a", "z", "n"), ("a", "empty"), ("new",)]


def test_project_reads_values_by_path_with_none_for_absent() -> None:
    document = {"a": {"y": [3]}, "new": "v"}
    assert project(document, (("a", "y"), ("new",), ("gone",), ("a", "y", "deep"))) == [
        [3],
        "v",
        None,
        None,
    ]


def test_remove_paths_prunes_mappings_it_empties() -> None:
    document = {"a": {"y": 1, "z": 2}, "b": {"only": 1}, "c": 3}
    remove_paths(document, (("a", "y"), ("b", "only"), ("nope", "x")))
    assert document == {"a": {"z": 2}, "c": 3}


def test_value_hash_ignores_key_order_and_whitespace() -> None:
    assert value_hash({"a": 1, "b": [1, 2]}) == value_hash({"b": [1, 2], "a": 1})
    assert value_hash({"a": 1}) != value_hash({"a": 2})
    assert content_hash(b"x").startswith("sha256:")


def test_yaml_merge_keeps_comments_and_order(tmp_path: Path) -> None:
    target = tmp_path / "t.yaml"
    target.write_text("# top\nname: app  # inline\nnested:\n  keep: 1\n  level: debug\n")
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    target_hash, managed = placer.merge(
        b"nested:\n  level: info\nnew: [1, 2]\n", target, fmt="yaml"
    )
    text = target.read_text()
    assert text.startswith("# top\nname: app  # inline\n")
    assert "level: info" in text
    assert managed == (("nested", "level"), ("new",))
    assert target_hash == value_hash(
        [
            "info",
            [1, 2],
        ]
    )
    placer.unmerge(target, fmt="yaml", managed=managed)
    assert load_document(target.read_text(), fmt="yaml", where="t") == {
        "name": "app",
        "nested": {"keep": 1},
    }


def test_json_merge_creates_a_missing_target_and_keeps_foreign_keys(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    placer.merge(b'{"enabledPlugins": {"mine": true}}', target, fmt="json")
    assert target.read_text() == '{\n  "enabledPlugins": {\n    "mine": true\n  }\n}\n'
    target.write_text('{"enabledPlugins": {"mine": true, "theirs": true}, "model": "x"}')
    placer.merge(b'{"enabledPlugins": {"mine": false}}', target, fmt="json")
    assert load_document(target.read_text(), fmt="json", where="t") == {
        "enabledPlugins": {"mine": False, "theirs": True},
        "model": "x",
    }


def test_dump_document_formats() -> None:
    assert dump_document({"a": 1}, fmt="json") == '{\n  "a": 1\n}\n'
    assert dump_document(load_document("a: 1\n", fmt="yaml", where="t"), fmt="yaml") == "a: 1\n"
    assert load_document("", fmt="json", where="t") == {}
    assert load_document("   \n", fmt="yaml", where="t") == {}
