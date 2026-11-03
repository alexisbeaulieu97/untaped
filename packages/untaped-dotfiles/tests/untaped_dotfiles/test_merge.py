"""The deep merge, its managed key paths, and the document formats."""

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from untaped_dotfiles import documents
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
    assert dump_document({"a": 1}, fmt="json", where="t") == '{\n  "a": 1\n}\n'
    assert (
        dump_document(load_document("a: 1\n", fmt="yaml", where="t"), fmt="yaml", where="t")
        == "a: 1\n"
    )
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
    assert tomllib.loads(target.read_text()) == {"a": {"c": 2}}


def test_toml_documents_load_dump_and_report_errors() -> None:
    assert load_document("", fmt="toml", where="t") == {}
    assert (
        dump_document(load_document("a = 1\n", fmt="toml", where="t"), fmt="toml", where="t")
        == "a = 1\n"
    )
    with pytest.raises(DotfilesError, match="t is invalid TOML"):
        load_document("a = = 1\n", fmt="toml", where="t")


#: Target and source shapes a merge meets; each is checked against the same merge on plain values.
_TOML_SHAPES: dict[str, tuple[str, str]] = {
    "a new table under dotted keys": ("a.b = 1\nother = 2\n", "[a.c]\nd = 1\n"),
    "a new table under dotted keys in a table": ("[t]\na.b = 1\nk = 2\n", "[t.a.c]\nd = 1\n"),
    "a new array of tables under dotted keys": ("a.b = 1\nother = 2\n", "[[a.s]]\nh = 1\n"),
    "a scalar under dotted keys becomes a table": ("a.b = 1\nother = 2\n", "[a.b]\nx = 1\n"),
    "a table under an implicit table": ("[x.y]\nz = 1\n", "[x.q]\nr = 1\n[x.y]\nz = 2\n"),
    "an inline table gains a table": ("t = {x = 1}\nk = 2\n", "[t.y]\nz = 1\n"),
    "an array of tables becomes a scalar": ("[[s]]\nh = 1\n[u]\nv = 1\n", "s = 3\n"),
    "a table becomes a scalar": ("[a]\nb = 1\n[c]\nd = 1\n", "a = 1\n"),
    "a scalar becomes a table": ("a = 1\n[c]\nd = 1\n", "[a]\nb = 1\n"),
    "tables out of order": ("[a]\nx = 1\n[b]\ny = 1\n[a.sub]\nz = 1\n", "[a.sub]\nw = 2\n"),
    "a table gains keys and a table": ("[n]\nk = 1\n[m]\nj = 1\n", "[n]\nsub.q = 1\nz = 1\n"),
    "split dotted keys gain a table": ("a.b = 1\na.s = 2\nother = 2\n", "[a.c]\nd = 1\n"),
    "an array of tables under an implicit table": ("[[tool.x]]\nh = 1\n", "[[tool.x]]\nh = 2\n"),
    "a deep new array of tables": ("", "[[a.b.c]]\ne = 1\n"),
    "an implicit table gains a table": (
        "[tool.x]\nh = 1\n",
        "[tool.y]\nz = 2\n[tool.y.w]\nq = 1\n",
    ),
    "split dotted keys: a table becomes a scalar": (
        "tool.ruff.x = 1\ntool.ruff.y = 2\ntool.z = 2\n",
        "[tool]\nruff = false\nz = 4\n",
    ),
    "unicode and dates": ('"clé" = "été"\n', "[t]\nat = 07:32:00\nday = 2024-01-02\n"),
}


def _reference_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(target)
    merge_into(merged, copy.deepcopy(source))
    return merged


@pytest.mark.parametrize("shape", _TOML_SHAPES, ids=list(_TOML_SHAPES))
def test_toml_merge_and_unmerge_read_back_as_plain_values_would(shape: str, tmp_path: Path) -> None:
    before, source = _TOML_SHAPES[shape]
    target = tmp_path / "config.toml"
    target.write_text(before)
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    target_hash, managed = placer.merge(source.encode(), target, fmt="toml")
    merged = target.read_text()
    assert tomllib.loads(merged) == _reference_merge(tomllib.loads(before), tomllib.loads(source))
    assert placer.observe(target, fmt="toml", managed=managed).hash == target_hash
    placer.merge(source.encode(), target, fmt="toml")
    assert target.read_text() == merged
    placer.unmerge(target, fmt="toml", managed=managed)
    expected = tomllib.loads(merged)
    remove_paths(expected, managed)
    assert tomllib.loads(target.read_text()) == expected


def test_a_header_table_stays_a_header_table(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text("[tool.x]\nh = 1\n")
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    placer.merge(b"[tool.y]\nz = 2\n", target, fmt="toml")
    assert target.read_text() == "[tool.x]\nh = 1\n\n[tool.y]\nz = 2\n"


def test_a_toml_merge_no_layout_can_hold_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.toml"
    target.write_text("a = 1\n")
    monkeypatch.setattr(documents, "_toml_text", lambda document, expected: None)
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    with pytest.raises(DotfilesError, match="nothing was written"):
        placer.merge(b"b = 2\n", target, fmt="toml")
    assert target.read_text() == "a = 1\n"


def test_a_toml_merge_tomlkit_would_lose_a_key_in_is_refused(tmp_path: Path) -> None:
    # Writing through an array of tables split around its table drops `d.c` in tomlkit.
    before = "[[d.b]]\na = 0\n[d]\nc = 6\n[[d.b]]\nc = 5\n"
    target = tmp_path / "config.toml"
    target.write_text(before)
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    with pytest.raises(DotfilesError, match="nothing was written"):
        placer.merge(b"[[d.b]]\nz = 1\n", target, fmt="toml")
    assert target.read_text() == before


def test_a_yaml_source_date_is_written_as_a_date_and_taken_back(tmp_path: Path) -> None:
    target = tmp_path / "t.yaml"
    target.write_text("keep: 1\n")
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    source = b"when: 2001-12-14\nat: 2001-12-14t21:59:43.10-05:00\nname: x\n"
    target_hash, managed = placer.merge(source, target, fmt="yaml")
    assert target.read_text() == (
        "keep: 1\nwhen: 2001-12-14\nat: 2001-12-14T21:59:43.100000-05:00\nname: x\n"
    )  # unquoted: the program reading it sees a date and a timestamp
    assert placer.observe(target, fmt="yaml", managed=managed).hash == target_hash
    placer.unmerge(target, fmt="yaml", managed=managed)
    assert target.read_text() == "keep: 1\n"


def test_yaml_source_values_hash_as_before_without_dates() -> None:
    text = "a: &on true\nb: *on\n1: one\nn: 0x1f\nf: 1.50\nl: [x, {y: 2}]\n"
    loaded = load_document(text, fmt="yaml", where="t")
    assert documents.load_values(text, fmt="yaml", where="t") == documents.plain(loaded)
    assert list(leaf_paths(documents.load_values(text, fmt="yaml", where="t")))[2] == ("1",)


@pytest.mark.parametrize(
    ("fmt", "target", "source"),
    [
        ("json", '{"a": {"x": 1}}', b'{"a": {}}'),
        ("yaml", "a:\n  x: 1\n", b"a: {}\n"),
        ("toml", "[a]\nx = 1\n", b"a = {}\n"),
        ("toml", "[a]\nx = 1\n", b"[a]\n"),
    ],
)
def test_an_empty_source_mapping_leaves_the_targets_keys_under_it(
    fmt: str, target: str, source: bytes, tmp_path: Path
) -> None:
    path = tmp_path / f"t.{fmt}"
    path.write_text(target)
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    target_hash, managed = placer.merge(source, path, fmt=fmt)  # type: ignore[arg-type]
    assert managed == (("a",),)
    edited = path.read_text().replace("1", "2")
    path.write_text(edited)
    observed = placer.observe(path, fmt=fmt, managed=managed)  # type: ignore[arg-type]
    assert observed.hash == target_hash  # a.x is the target's: editing it changes nothing
    placer.unmerge(path, fmt=fmt, managed=managed)  # type: ignore[arg-type]
    assert load_document(path.read_text(), fmt=fmt, where="t") == {"a": {"x": 2}}  # type: ignore[arg-type]


def test_an_empty_mapping_the_merge_creates_is_taken_back(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text('{"keep": 1}')
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    _, managed = placer.merge(b'{"a": {}}', path, fmt="json")
    assert json.loads(path.read_text()) == {"keep": 1, "a": {}}
    placer.unmerge(path, fmt="json", managed=managed)
    assert json.loads(path.read_text()) == {"keep": 1}


def test_an_aliased_yaml_date_is_written_as_plain_dates(tmp_path: Path) -> None:
    target = tmp_path / "t.yaml"
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    source = (
        b"a: &d 2001-12-14\nb: *d\nc: [*d]\nd: {e: *d}\nt: &t 2001-12-14t21:59:43.10-05:00\nu: *t\n"
    )
    placer.merge(source, target, fmt="yaml")
    assert target.read_text() == (
        "a: 2001-12-14\nb: 2001-12-14\nc:\n- 2001-12-14\nd:\n  e: 2001-12-14\n"
        "t: 2001-12-14T21:59:43.100000-05:00\nu: 2001-12-14T21:59:43.100000-05:00\n"
    )


def test_a_nested_empty_source_mapping_leaves_the_targets_keys_under_it(tmp_path: Path) -> None:
    path = tmp_path / "t.yaml"
    path.write_text("a:\n  b:\n    x: 1\n  keep: 2\n")
    placer = FilesystemPlacer(home=tmp_path, kept_dir=tmp_path / "kept")
    target_hash, managed = placer.merge(b"a: {b: {}}\n", path, fmt="yaml")
    assert managed == (("a", "b"),)
    path.write_text(path.read_text().replace("x: 1", "x: 3"))
    assert placer.observe(path, fmt="yaml", managed=managed).hash == target_hash
    placer.unmerge(path, fmt="yaml", managed=managed)
    assert load_document(path.read_text(), fmt="yaml", where="t") == {
        "a": {"b": {"x": 3}, "keep": 2}
    }
