"""The deep merge, its managed key paths, and the document formats."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_dotfiles.documents import dump_document, load_document
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.hashing import content_hash, value_hash
from untaped_dotfiles.merge import leaf_paths, merge_into, project, remove_paths
from untaped_dotfiles.placer import FilesystemPlacer


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


def test_yaml_merge_keeps_the_target_list_indentation(tmp_path: Path) -> None:
    target = tmp_path / "t.yaml"
    target.write_text("items:\n- a\n- b\nkeep: 1\n")
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    placer.merge(b"new: [x, y]\n", target, fmt="yaml")
    assert target.read_text() == "items:\n- a\n- b\nkeep: 1\nnew:\n- x\n- y\n"


def test_yaml_merge_keeps_the_target_mapping_indentation(tmp_path: Path) -> None:
    target = tmp_path / "t.yaml"
    target.write_text("outer:\n    inner: 1\n")
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    placer.merge(b"added: {deep: 2}\n", target, fmt="yaml")
    assert target.read_text() == "outer:\n    inner: 1\nadded:\n    deep: 2\n"


_TOML_TARGET = """\
# top
name = "app"  # inline
skip = [1, 2]

[nested]
keep = 1
level = "debug"  # why

[[servers]]
host = "a"
"""


def test_toml_merge_keeps_comments_and_order_and_replaces_arrays(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text(_TOML_TARGET)
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    source = b'skip = [3]\nnew = 1\n[nested]\nlevel = "info"\n[[servers]]\nhost = "b"\n'
    target_hash, managed = placer.merge(source, target, fmt="toml")
    assert target.read_text() == (
        "# top\n"
        'name = "app"  # inline\n'
        "skip = [3]\n"
        "new = 1\n"
        "\n"
        "[nested]\n"
        "keep = 1\n"
        'level = "info"  # why\n'
        "\n"
        "[[servers]]\n"
        'host = "b"\n'
    )
    assert managed == (("skip",), ("new",), ("nested", "level"), ("servers",))
    assert target_hash == value_hash([[3], 1, "info", [{"host": "b"}]])
    assert placer.observe(target, fmt="toml", managed=managed).hash == target_hash


def test_toml_unmerge_takes_managed_keys_back_out(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text(_TOML_TARGET)
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    source = b'new = 1\n[nested]\nlevel = "info"\n[tool.deep]\nx = 2\n'
    _, managed = placer.merge(source, target, fmt="toml")
    assert "[tool.deep]\nx = 2\n" in target.read_text()
    placer.unmerge(target, fmt="toml", managed=managed)
    text = target.read_text()
    assert text.startswith('# top\nname = "app"  # inline\nskip = [1, 2]\n')
    assert load_document(text, fmt="toml", where="t") == {
        "name": "app",
        "skip": [1, 2],
        "nested": {"keep": 1},
        "servers": [{"host": "a"}],
    }


def test_toml_merge_creates_a_missing_target_and_keeps_dates(tmp_path: Path) -> None:
    target = tmp_path / "new.toml"
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    placer.merge(b"[when]\nday = 2024-01-02\nat = 1979-05-27T07:32:00Z\n", target, fmt="toml")
    assert target.read_text() == "[when]\nday = 2024-01-02\nat = 1979-05-27T07:32:00Z\n"


def test_toml_dotted_keys_and_super_tables_merge_in_place(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text("a.b = 1\na.c = 2\n\n[x.y]\nz = 1\n")
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    _, managed = placer.merge(b"a.b = 5\n[x.y]\nz = 9\n", target, fmt="toml")
    assert target.read_text() == "a.b = 5\na.c = 2\n\n[x.y]\nz = 9\n"
    placer.unmerge(target, fmt="toml", managed=managed)
    assert target.read_text() == "a.c = 2\n\n"


def test_toml_documents_load_dump_and_report_errors() -> None:
    assert load_document("", fmt="toml", where="t") == {}
    assert dump_document(load_document("a = 1\n", fmt="toml", where="t"), fmt="toml") == "a = 1\n"
    with pytest.raises(DotfilesError, match="t is invalid TOML"):
        load_document("a = = 1\n", fmt="toml", where="t")
