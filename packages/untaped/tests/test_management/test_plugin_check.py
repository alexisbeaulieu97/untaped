"""``untaped plugin check``: conventions, conformance, a live call, fills and the schema hash."""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent

import pytest

from untaped import bootstrap
from untaped.contracts._registry import doctor_rows
from untaped.management.plugin_check import PluginCheckRow, check_plugins, report_check_rows
from untaped.plugins.registry import PluginCandidate

pytestmark = pytest.mark.usefixtures("fresh_composition", "_isolated_config")

_RACK = {
    "untaped_rack/__init__.py": '''
        """An owner: where items come from."""

        from untaped.sdk import PluginSpec


        def _contracts():
            from untaped_rack.api import ItemSource

            return (ItemSource,)


        SPEC = PluginSpec(name="rack", contracts=_contracts)
        ''',
    "untaped_rack/errors.py": '"""Errors."""\n',
    "untaped_rack/api.py": '''
        """The rack contract."""

        from abc import abstractmethod

        from untaped.contracts import Contract, Issued, Record, bridge


        class Item(Issued, kind="rack.item"):
            name: str


        class ItemSource[T: Record = Item](Contract):
            """Where items come from."""

            @bridge
            def to_item(self, item: T) -> Item:
                raise NotImplementedError

            @abstractmethod
            def items(self) -> list[Item]: ...

            def named(self, name: str) -> list[Item]:
                raise NotImplementedError
        ''',
    "untaped_rack/testing.py": '''
        """The rack's conformance checks."""


        def conformance(provider):
            if getattr(type(provider), "nonconforming", False):
                raise AssertionError("items must be sorted")
        ''',
}

_BIN = {
    "untaped_bin/__init__.py": '''
        """A provider for rack."""

        from untaped.sdk import PluginSpec


        def _rack():
            from untaped_bin.adapters.rack import BinSource

            return (BinSource(),)


        SPEC = PluginSpec(name="bin", provides={"rack": _rack})
        ''',
    "untaped_bin/errors.py": '"""Errors."""\n',
    "untaped_bin/adapters/__init__.py": "",
    "untaped_bin/adapters/rack.py": '''
        """Bins as rack items."""

        from typing import ClassVar

        from untaped.contracts import Record
        from untaped_rack.api import Item, ItemSource


        class Box(Record, kind="bin.box"):
            id: int
            label: str


        class BinSource(ItemSource[Box]):
            boxes: ClassVar[list[Box]] = [Box(id=1, label="tools"), Box(id=2, label="toys")]
            nonconforming: ClassVar[bool] = False
            broken: ClassVar[bool] = False

            def to_item(self, item: Box) -> Item:
                return Item(name=item.label)

            def items(self) -> list[Item]:
                if type(self).broken:
                    raise RuntimeError("the bin is locked")
                return [self.to_item(box) for box in type(self).boxes]
        ''',
}

_CORE_RANGE = "untaped>=10,<11"


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """``untaped_rack`` and ``untaped_bin`` on ``sys.path``; unloaded after the test."""
    site = tmp_path / "site"
    for name, body in {**_RACK, **_BIN}.items():
        path = site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(body).lstrip(), encoding="utf-8")
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    yield site
    for module in [
        name for name in sys.modules if name.split(".")[0] in {"untaped_rack", "untaped_bin"}
    ]:
        del sys.modules[module]


def _candidates(
    *, rack: bool = True, bin_requires: tuple[str, ...] | None = None
) -> list[PluginCandidate]:
    requires = bin_requires or (_CORE_RANGE, "untaped-rack>=1,<2; extra == 'rack'")
    found = [
        PluginCandidate(
            distribution="untaped-bin",
            name="bin",
            target="untaped_bin:SPEC",
            distribution_version="1.0",
            requires_dist=requires,
        )
    ]
    if rack:
        found.append(
            PluginCandidate(
                distribution="untaped-rack",
                name="rack",
                target="untaped_rack:SPEC",
                distribution_version="1.4",
                requires_dist=(_CORE_RANGE,),
            )
        )
    return found


def _check(name: str | None = "bin", **kwargs: object) -> list[PluginCheckRow]:
    candidates = _candidates(**kwargs)  # type: ignore[arg-type]
    result = bootstrap.compose_root(candidates=candidates)
    return check_plugins(result, candidates, name)


def _rows(rows: list[PluginCheckRow]) -> list[tuple[str, str, str, str]]:
    return [(row.check, row.title, row.status, row.detail) for row in rows]


def _bin() -> type:
    return importlib.import_module("untaped_bin.adapters.rack").BinSource


def _record(install: Path, value: str) -> None:
    (install / "untaped_bin" / "fills.json").write_text(
        json.dumps({"untaped": "1", "fills": {"rack.item_source": value}}), encoding="utf-8"
    )


def _current_hash() -> str:
    from untaped.contracts._declare import contract_of
    from untaped.contracts._schema import schema_hash

    info = contract_of(importlib.import_module("untaped_rack.api").ItemSource)
    assert info is not None
    return schema_hash(info)


def test_a_provider_that_fills_its_contract_passes_every_check(install: Path) -> None:
    _record(install, _current_hash())
    assert _rows(_check()) == [
        ("conventions", "conventions", "pass", "no violations"),
        ("conformance", "rack.item_source", "pass", "rack's conformance checks pass"),
        ("live", "rack.item_source.items", "pass", "2 items"),
        ("fills", "rack.item_source", "pass", "2 live items"),
        ("schema", "rack.item_source", "pass", "tested against this owner's schema"),
    ]


def test_without_a_name_every_installed_plugin_is_checked(install: Path) -> None:
    _record(install, _current_hash())
    rows = _check(None)
    assert sorted({row.plugin for row in rows}) == ["bin", "rack"]
    assert ("contracts", "rack", "pass", "declares item_source") in _rows(
        [row for row in rows if row.plugin == "rack"]
    )


def test_an_unknown_plugin_is_a_usage_error(install: Path) -> None:
    from untaped.errors import UsageError

    with pytest.raises(UsageError, match="nowhere"):
        _check("nowhere")


def test_failures_are_reported_and_drift_is_a_warning(install: Path) -> None:
    _record(install, "0" * 64)
    _bin().nonconforming = True
    _bin().broken = True
    rows = _rows(_check())
    assert ("conformance", "rack.item_source", "fail", "items must be sorted") in rows
    assert ("live", "rack.item_source.items", "fail", "RuntimeError: the bin is locked") in rows
    assert ("fills", "rack.item_source", "pass", "skipped: no live items to check") in rows
    schema = next(row for row in rows if row[0] == "schema")
    assert schema[2] == "warn"
    assert schema[3].startswith("owner-schema-drift: ")


def test_no_recorded_hash_is_a_warning(install: Path) -> None:
    schema = next(row for row in _rows(_check()) if row[0] == "schema")
    assert schema[2:] == ("warn", "no recorded owner schema hash; assert_fills records it")


def test_an_owner_that_is_not_installed_skips_the_offer(install: Path) -> None:
    rows = _rows(_check(rack=False))
    assert rows[-1][0] == "registration"
    assert rows[-1][2] == "pass"
    assert rows[-1][3].startswith("skipped: ")


def test_an_owner_out_of_range_fails_registration(install: Path) -> None:
    rows = _rows(_check(bin_requires=(_CORE_RANGE, "untaped-rack>=2,<3; extra == 'rack'")))
    assert (
        "registration",
        "rack",
        "fail",
        "owner-out-of-range: bin requires untaped-rack<3,>=2 for rack, but 1.4 is installed",
    ) in rows


def test_the_check_is_hermetic_and_keeps_the_composition(install: Path) -> None:
    candidates = _candidates()
    result = bootstrap.compose_root(candidates=candidates)
    check_plugins(result, candidates, "bin")
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


def test_doctor_reports_owner_schema_drift(install: Path) -> None:
    _record(install, "0" * 64)
    bootstrap.compose_root(candidates=_candidates())
    drift = [row for row in doctor_rows() if row.title == "owner-schema-drift"]
    assert len(drift) == 1
    assert drift[0].status == "warn"
    _record(install, _current_hash())
    assert not [row for row in doctor_rows() if row.title == "owner-schema-drift"]
