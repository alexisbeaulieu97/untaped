"""RefDelta: added, moved and pruned, and falsy when nothing changed."""

from __future__ import annotations

from untaped_git.domain.delta import RefDelta, RefMove, diff_refs


def test_diff_refs() -> None:
    delta = diff_refs(
        {"heads/a": "1", "heads/b": "2", "tags/t": "3"},
        {"heads/b": "4", "heads/a": "1", "heads/c": "5"},
    )
    assert dict(delta.added) == {"heads/c": "5"}
    assert dict(delta.moved) == {"heads/b": RefMove("2", "4")}
    assert dict(delta.pruned) == {"tags/t": "3"}
    assert delta


def test_an_empty_delta_is_falsy() -> None:
    assert not diff_refs({"heads/a": "1"}, {"heads/a": "1"})
    assert not RefDelta()


def test_lists_are_sorted() -> None:
    delta = diff_refs({}, {"heads/b": "2", "heads/a": "1"})
    assert list(delta.added) == ["heads/a", "heads/b"]
