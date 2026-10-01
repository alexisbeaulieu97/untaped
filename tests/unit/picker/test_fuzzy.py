"""Fuzzy ranking for the picker."""

from __future__ import annotations

from untaped.picker import PickItem
from untaped.picker.fuzzy import fuzzy_match, rank


def _items(*labels: str, dimmed: tuple[str, ...] = ()) -> list[PickItem]:
    return [PickItem(id=label, label=label, dimmed=label in dimmed) for label in labels]


def test_empty_query_matches_everything_with_no_positions() -> None:
    match = fuzzy_match("", "acme/api")
    assert match is not None
    assert match.positions == ()


def test_letters_out_of_order_do_not_match() -> None:
    assert fuzzy_match("ipa", "acme/api") is None


def test_substring_reports_contiguous_positions() -> None:
    match = fuzzy_match("API", "acme/api")
    assert match is not None
    assert match.positions == (5, 6, 7)


def test_subsequence_match_reports_each_matched_letter() -> None:
    match = fuzzy_match("agw", "acme/api-gateway")
    assert match is not None
    assert [("acme/api-gateway")[i] for i in match.positions] == ["a", "g", "w"]


def test_substring_at_a_word_boundary_ranks_first() -> None:
    ranked = rank("api", _items("legacy/rapid", "acme/api-docs", "acme/api"))
    assert [r.item.label for r in ranked][:2] == ["acme/api", "acme/api-docs"]


def test_dimmed_items_sort_last_even_when_they_match_better() -> None:
    ranked = rank("api", _items("legacy/api", "acme/api-gateway", dimmed=("legacy/api",)))
    assert [r.item.label for r in ranked] == ["acme/api-gateway", "legacy/api"]


def test_no_query_keeps_catalog_order_with_dimmed_last() -> None:
    ranked = rank("", _items("b", "a", "c", dimmed=("b",)))
    assert [r.item.label for r in ranked] == ["a", "c", "b"]


def test_every_term_must_match_label_or_description() -> None:
    items = [
        PickItem(id="1", label="acme/api", description="Core REST API"),
        PickItem(id="2", label="acme/web", description="Frontend"),
    ]
    assert [r.item.id for r in rank("acme rest", items)] == ["1"]
    assert rank("acme nothing", items) == []


def test_description_matches_need_a_substring() -> None:
    items = [PickItem(id="1", label="acme/web", description="Frontend")]
    assert rank("fe", items) == []  # "fe" is not a substring of "frontend" or the label
    assert [r.item.id for r in rank("front", items)] == ["1"]
