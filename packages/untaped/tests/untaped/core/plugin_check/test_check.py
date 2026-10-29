"""The plugin checks: conventions, conformance and a live call."""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path

import pytest

from core_test_support.commands.contract_plugins import CORE_RANGE, candidates
from core_test_support.commands.support import make_plugin
from untaped.core.plugin_check.check import PluginCheck, check_plugins
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.contracts import Provider
from untaped.sdk.testing import assert_fills, compose_with, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition", "_isolated_config")


def _check(name: str | None = "bin", **kwargs: object) -> list[PluginCheck]:
    found = candidates(**kwargs)  # type: ignore[arg-type]
    result = bootstrap.compose_root(candidates=found)
    return check_plugins(result, found, name)


def _rows(rows: list[PluginCheck]) -> list[tuple[str, str, str, str]]:
    return [(row.check, row.title, row.status, row.detail) for row in rows]


def _bin() -> type:
    return importlib.import_module("untaped_bin.providers.rack").BinSource


def test_a_provider_that_fills_its_contract_passes_every_check(contract_plugins_site: Path) -> None:
    assert _rows(_check()) == [
        ("conventions", "conventions", "pass", "no violations"),
        ("conformance", "rack.item_source", "pass", "rack's conformance checks pass"),
        ("live", "rack.item_source.items", "pass", "2 items"),
        ("live", "rack.item_source.named", "pass", "skipped: it needs arguments"),
    ]


def test_without_a_name_every_installed_plugin_is_checked(contract_plugins_site: Path) -> None:
    rows = _check(None)
    assert sorted({row.plugin for row in rows}) == ["bin", "rack"]
    assert ("contracts", "rack", "pass", "declares item_source") in _rows(
        [row for row in rows if row.plugin == "rack"]
    )


def test_an_unknown_plugin_is_a_usage_error(contract_plugins_site: Path) -> None:
    from untaped.sdk.errors import UsageError

    with pytest.raises(UsageError, match="nowhere"):
        _check("nowhere")


def test_failures_are_reported(contract_plugins_site: Path) -> None:
    _bin().nonconforming = True
    _bin().broken = True
    rows = _rows(_check())
    assert ("conformance", "rack.item_source", "fail", "items must be sorted") in rows
    assert ("live", "rack.item_source.items", "fail", "RuntimeError: the bin is locked") in rows
    assert not [row for row in rows if row[0] in {"fills", "schema"}]


def test_a_fill_whose_owner_is_not_installed_fails_registration(
    contract_plugins_site: Path,
) -> None:
    rows = _rows(_check(rack=False))
    assert rows[-1] == (
        "registration",
        "ItemSource",
        "fail",
        "fills untaped_rack._contract:ItemSource, which no installed plugin offers",
    )


def test_an_owner_out_of_range_fails_registration(contract_plugins_site: Path) -> None:
    rows = _rows(_check(bin_requires=(CORE_RANGE, "untaped-rack>=2,<3")))
    assert (
        "registration",
        "rack",
        "fail",
        "owner-out-of-range: bin requires untaped-rack>=2,<3 for rack, but 1.4 is installed",
    ) in rows


def test_the_check_is_hermetic_and_keeps_the_composition(contract_plugins_site: Path) -> None:
    found = candidates()
    result = bootstrap.compose_root(candidates=found)
    check_plugins(result, found, "bin")
    assert bootstrap.composition() is result


def test_a_provider_that_is_not_ready_skips_the_calls(contract_plugins_site: Path) -> None:
    _bin().waiting = True
    rows = _rows(_check())
    waiting = "skipped: not ready: no bins yet (set bin.bins)"
    assert ("conformance", "rack.item_source", "pass", waiting) in rows
    assert ("live", "rack.item_source.items", "pass", waiting) in rows


def test_a_convention_violation_is_a_failed_row(contract_plugins_site: Path) -> None:
    (contract_plugins_site / "untaped_bin" / "leak.py").write_text(
        "class Leak(Exception):\n    pass\n", encoding="utf-8"
    )
    found = [row for row in _check() if row.check == "conventions"]
    [violation] = [row for row in found if row.title == "exception-base"]
    assert violation.status == "fail"
    assert "untaped_bin.leak.Leak" in violation.detail


def test_a_quarantined_plugin_fails_registration(contract_plugins_site: Path) -> None:
    broken = PluginCandidate(distribution="untaped-gone", name="gone", target="untaped_gone:Plugin")
    found = [*candidates(), broken]
    result = bootstrap.compose_root(candidates=found)
    [row] = check_plugins(result, found, "gone")
    assert (row.check, row.status) == ("registration", "fail")


def test_assert_fills_finds_the_installed_plugin_and_its_owner(
    contract_plugins_site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("untaped.runtime.registry.discover_candidates", lambda: candidates())
    with compose_with() as result:
        assert sorted(plugin.name for plugin in result.plugins) == ["bin", "rack", "untaped"]
    box = importlib.import_module("untaped_bin.providers.rack").Box
    assert_fills(_bin(), samples=[box(id=3, label="pens")])
    assert not (contract_plugins_site / "untaped_bin" / "fills.json").exists()


@pytest.mark.parametrize("by_class", [False, True], ids=["entry-point", "class"])
def test_a_provider_fails_when_its_owner_is_quarantined(
    contract_plugins_site: Path, by_class: bool
) -> None:
    """Once, named by the owner, whether its candidate names the class or holds it."""
    rack = importlib.import_module("untaped_rack.plugin").Rack if by_class else None
    found = [
        candidate
        if candidate.name != "rack"
        else PluginCandidate(
            distribution="untaped-rack",
            name="rack",
            target=rack or "untaped_rack.plugin:Rack",
            distribution_version="1.4",
            requires_dist=("untaped>=99",),
        )
        for candidate in candidates()
    ]
    result = bootstrap.compose_root(candidates=found)
    [registration] = [row for row in check_plugins(result, found, "bin") if row.title == "rack"]
    assert not [row for row in check_plugins(result, found, "bin") if "offers" in row.detail]
    assert registration.status == "fail"
    assert registration.detail.startswith("rack is quarantined (")


def test_two_fills_of_a_quarantined_owner_give_one_row(contract_plugins_site: Path) -> None:
    api = importlib.import_module("untaped_rack.api")

    class Left(api.ItemSource, Provider):  # type: ignore[misc,name-defined]
        def items(self) -> list[object]:
            return []

    class Right(api.ItemSource, Provider):  # type: ignore[misc,name-defined]
        def items(self) -> list[object]:
            return []

    duo = make_plugin("duo", fills=(Left, Right))
    found = [
        candidate
        if candidate.name != "rack"
        else dataclasses.replace(candidate, requires_dist=("untaped>=99",))
        for candidate in [*candidates(), plugin_candidate(duo)]
    ]
    result = bootstrap.compose_root(candidates=found)
    rows = [row for row in check_plugins(result, found, "duo") if row.check == "registration"]
    assert [(row.title, row.status) for row in rows] == [("rack", "fail")]


def test_a_provider_of_the_owners_own_model_is_checked_on_its_items(
    contract_plugins_site: Path,
) -> None:
    api = importlib.import_module("untaped_rack.api")

    class Shelf(api.ItemSource, Provider):  # type: ignore[misc,name-defined]
        def items(self) -> list[object]:
            return [api.Item(name="cup")]

    shelf = make_plugin("shelf", fills=(Shelf,))
    found = [*candidates(), plugin_candidate(shelf)]
    result = bootstrap.compose_root(candidates=found)
    rows = _rows(check_plugins(result, found, "shelf"))
    assert ("live", "rack.item_source.items", "pass", "1 item") in rows
