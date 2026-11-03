"""The ``Vars`` block: files in order, then ``--var`` pairs over them."""

from __future__ import annotations

from pathlib import Path

from untaped_values.api import Vars


def test_values_read_the_files_in_order_then_the_pairs(tmp_path: Path) -> None:
    first = tmp_path / "first.yml"
    first.write_text("a: 1\nb: 1\nc: 1\n")
    second = tmp_path / "second.yml"
    second.write_text("b: 2\nc: 2\n")

    block = Vars(var=[("c", "3")], vars_file=[first, second])

    assert block.values() == {"a": 1, "b": 2, "c": "3"}
