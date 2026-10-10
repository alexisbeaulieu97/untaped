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

pytestmark = pytest.mark.usefixtures("fresh_composition", "_isolated_config")


def _check(name: str | None = "bin", **kwargs: object) -> list[PluginCheckRow]:
    found = candidates(**kwargs)  # type: ignore[arg-type]
    result = bootstrap.compose_root(candidates=found)
    return check_plugins(result, found, name)


def _rows(rows: list[PluginCheckRow]) -> list[tuple[str, str, str, str]]:
    return [(row.check, row.title, row.status, row.detail) for row in rows]


def _bin() -> type:
    return importlib.import_module("untaped_bin.adapters.rack").BinSource


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
