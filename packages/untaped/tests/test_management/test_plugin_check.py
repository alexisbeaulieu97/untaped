"""``untaped plugin check``: conventions, conformance, a live call, fills and the schema hash."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from test_management.contract_plugins import CORE_RANGE, candidates
from untaped import bootstrap
from untaped.contracts._registry import doctor_rows
from untaped.management.plugin_check import PluginCheckRow, check_plugins, report_check_rows
from untaped.plugins.registry import PluginCandidate, PluginSpec
from untaped.testing import assert_fills, compose_with, invoke_cli, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition", "_isolated_config")


def _check(name: str | None = "bin", **kwargs: object) -> list[PluginCheckRow]:
    found = candidates(**kwargs)  # type: ignore[arg-type]
    result = bootstrap.compose_root(candidates=found)
    return check_plugins(result, found, name)


def _rows(rows: list[PluginCheckRow]) -> list[tuple[str, str, str, str]]:
    return [(row.check, row.title, row.status, row.detail) for row in rows]


def _bin() -> type:
    return importlib.import_module("untaped_bin.providers.rack").BinSource


def _record(site: Path, value: str) -> None:
    (site / "untaped_bin" / "fills.json").write_text(
        json.dumps({"untaped": "1", "fills": {"rack.item_source": value}}), encoding="utf-8"
    )


def _current_hash() -> str:
    from untaped.contracts._declare import contract_of
    from untaped.contracts._schema import schema_hash

    info = contract_of(importlib.import_module("untaped_rack.api").ItemSource)
    assert info is not None
    return schema_hash(info)


def test_a_provider_that_fills_its_contract_passes_every_check(contract_plugins_site: Path) -> None:
    _record(contract_plugins_site, _current_hash())
    assert _rows(_check()) == [
        ("conventions", "conventions", "pass", "no violations"),
        ("conformance", "rack.item_source", "pass", "rack's conformance checks pass"),
        ("live", "rack.item_source.items", "pass", "2 items"),
        ("live", "rack.item_source.named", "pass", "skipped: it needs arguments"),
        ("fills", "rack.item_source", "pass", "2 live items"),
        ("schema", "rack.item_source", "pass", "tested against this owner's schema"),
    ]


def test_without_a_name_every_installed_plugin_is_checked(contract_plugins_site: Path) -> None:
    _record(contract_plugins_site, _current_hash())
    rows = _check(None)
    assert sorted({row.plugin for row in rows}) == ["bin", "rack"]
    assert ("contracts", "rack", "pass", "declares item_source") in _rows(
        [row for row in rows if row.plugin == "rack"]
    )


def test_an_unknown_plugin_is_a_usage_error(contract_plugins_site: Path) -> None:
    from untaped.errors import UsageError

    with pytest.raises(UsageError, match="nowhere"):
        _check("nowhere")


def test_failures_are_reported_and_drift_is_a_warning(contract_plugins_site: Path) -> None:
    _record(contract_plugins_site, "0" * 64)
    _bin().nonconforming = True
    _bin().broken = True
    rows = _rows(_check())
    assert ("conformance", "rack.item_source", "fail", "items must be sorted") in rows
    assert ("live", "rack.item_source.items", "fail", "RuntimeError: the bin is locked") in rows
    assert ("fills", "rack.item_source", "pass", "skipped: no live items to check") in rows
    schema = next(row for row in rows if row[0] == "schema")
    assert schema[2] == "warn"
    assert schema[3].startswith("owner-schema-drift: ")


def test_no_recorded_hash_is_a_warning(contract_plugins_site: Path) -> None:
    schema = next(row for row in _rows(_check()) if row[0] == "schema")
    assert schema[2:] == ("warn", "no recorded owner schema hash; assert_fills records it")


def test_an_owner_that_is_not_installed_skips_the_offer(contract_plugins_site: Path) -> None:
    rows = _rows(_check(rack=False))
    assert rows[-1][0] == "registration"
    assert rows[-1][2] == "pass"
    assert rows[-1][3].startswith("skipped: ")


def test_an_owner_out_of_range_fails_registration(contract_plugins_site: Path) -> None:
    rows = _rows(_check(bin_requires=(CORE_RANGE, "untaped-rack>=2,<3; extra == 'rack'")))
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


def test_report_exits_one_on_a_failure_only(capsys: pytest.CaptureFixture[str]) -> None:
    passing = PluginCheckRow(plugin="bin", check="live", title="x", status="pass")
    warning = PluginCheckRow(plugin="bin", check="schema", title="x", status="warn")
    report_check_rows([passing, warning], fmt="json", columns=None)
    assert [row["status"] for row in json.loads(capsys.readouterr().out)] == ["pass", "warn"]
    with pytest.raises(SystemExit) as exited:
        report_check_rows(
            [PluginCheckRow(plugin="bin", check="live", title="x", status="fail")],
            fmt="json",
            columns=None,
        )
    assert exited.value.code == 1


def test_doctor_reports_owner_schema_drift(contract_plugins_site: Path) -> None:
    _record(contract_plugins_site, "0" * 64)
    bootstrap.compose_root(candidates=candidates())
    drift = [row for row in doctor_rows() if row.title == "owner-schema-drift"]
    assert len(drift) == 1
    assert drift[0].status == "warn"
    _record(contract_plugins_site, _current_hash())
    assert not [row for row in doctor_rows() if row.title == "owner-schema-drift"]


def test_a_provider_that_is_not_ready_skips_the_calls(contract_plugins_site: Path) -> None:
    _bin().waiting = True
    rows = _rows(_check())
    waiting = "skipped: not ready: no bins yet (set bin.bins)"
    assert ("conformance", "rack.item_source", "pass", waiting) in rows
    assert ("live", "rack.item_source.items", "pass", waiting) in rows


def test_a_convention_violation_is_a_failed_row(contract_plugins_site: Path) -> None:
    (contract_plugins_site / "untaped_bin" / "leak.py").write_text(
        "from untaped.bootstrap import composition\n", encoding="utf-8"
    )
    [violation] = [row for row in _check() if row.check == "conventions"]
    assert violation.status == "fail"
    assert violation.title == "import-boundary"
    assert "untaped.bootstrap" in violation.detail


def test_a_quarantined_plugin_fails_registration(contract_plugins_site: Path) -> None:
    broken = PluginCandidate(distribution="untaped-gone", name="gone", target="untaped_gone:SPEC")
    found = [*candidates(), broken]
    result = bootstrap.compose_root(candidates=found)
    [row] = check_plugins(result, found, "gone")
    assert (row.check, row.status) == ("registration", "fail")


def test_an_owner_whose_contracts_fail_is_reported(contract_plugins_site: Path) -> None:
    importlib.import_module("untaped_rack").BROKEN = True
    rows = _rows(_check("rack"))
    assert ("contracts", "rack", "fail", "its contracts function fails; see doctor") in rows


def test_the_command_prints_a_checklist_and_exits_one_on_a_failure(
    contract_plugins_site: Path,
) -> None:
    _record(contract_plugins_site, _current_hash())
    app = bootstrap.build_root_app(candidates=candidates()).meta
    passed = invoke_cli(app, ["plugin", "check", "bin"])
    assert passed.exit_code == 0, passed.output
    assert "rack.item_source.items" in passed.stdout
    assert "plugin check: 6 pass" in passed.stderr
    _bin().broken = True
    app = bootstrap.build_root_app(candidates=candidates()).meta
    failed = invoke_cli(app, ["plugin", "check", "bin", "--format", "json"])
    assert failed.exit_code == 1
    assert {row["status"] for row in json.loads(failed.stdout)} == {"pass", "fail"}


def test_assert_fills_finds_the_installed_plugin_and_its_owner(
    contract_plugins_site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("untaped.plugins.registry.discover_candidates", lambda: candidates())
    with compose_with() as result:
        assert sorted(plugin.spec.name for plugin in result.plugins) == ["bin", "rack"]
    box = importlib.import_module("untaped_bin.providers.rack").Box
    assert_fills(_bin(), samples=[box(id=3, label="pens")])
    recorded = json.loads((contract_plugins_site / "untaped_bin" / "fills.json").read_text("utf-8"))
    assert recorded["fills"] == {"rack.item_source": _current_hash()}


def test_a_provider_fails_when_its_owner_cannot_be_read(contract_plugins_site: Path) -> None:
    importlib.import_module("untaped_rack").BROKEN = True
    rows = _rows(_check())
    assert ("registration", "rack", "fail", "rack's contracts function fails; see doctor") in rows
    assert not [row for row in rows if row[0] in {"live", "fills", "schema"}]


def test_a_provider_fails_when_its_owner_is_quarantined(contract_plugins_site: Path) -> None:
    found = [
        candidate
        if candidate.name != "rack"
        else PluginCandidate(
            distribution="untaped-rack",
            name="rack",
            target="untaped_rack:SPEC",
            distribution_version="1.4",
            requires_dist=("untaped>=99",),
        )
        for candidate in candidates()
    ]
    result = bootstrap.compose_root(candidates=found)
    [registration] = [row for row in check_plugins(result, found, "bin") if row.title == "rack"]
    assert registration.status == "fail"
    assert registration.detail.startswith("rack is quarantined (")


def test_a_provider_of_the_owners_own_model_is_checked_on_its_items(
    contract_plugins_site: Path,
) -> None:
    api = importlib.import_module("untaped_rack.api")

    class Shelf(api.ItemSource):  # type: ignore[misc,name-defined]
        def items(self) -> list[object]:
            return [api.Item(name="cup")]

    shelf = PluginSpec(name="shelf", provides={"rack": lambda: (Shelf(),)})
    found = [*candidates(), plugin_candidate(shelf)]
    result = bootstrap.compose_root(candidates=found)
    rows = _rows(check_plugins(result, found, "shelf"))
    assert ("live", "rack.item_source.items", "pass", "1 item") in rows
    assert ("fills", "rack.item_source", "pass", "1 live item") in rows


def test_assert_fills_names_an_owner_whose_contracts_fail(contract_plugins_site: Path) -> None:
    importlib.import_module("untaped_rack").BROKEN = True
    box = importlib.import_module("untaped_bin.providers.rack").Box
    with (
        compose_with(*candidates()),
        pytest.raises(LookupError, match="whose owner may be rack: its contracts function fails"),
    ):
        assert_fills(_bin(), samples=[box(id=3, label="pens")])
