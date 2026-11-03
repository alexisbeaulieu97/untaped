"""Each dotfiles record takes its kind from its class name and keeps its rows' keys in order."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.sdk import Failure, UntapedError
from untaped.sdk.testing import install
from untaped_dotfiles.plugin import Dotfiles
from untaped_dotfiles.records import (
    ApplyOutcome,
    EntryStatus,
    ItemDetail,
    Package,
    PackageDetail,
    StatusSummary,
)

_PLACE = {
    "source": "dotfiles",
    "package": "fish",
    "item": "config",
    "entry": "~/.config/fish/config.fish",
    "mode": "link",
    "action": "created",
    "state": "pending",
    "target_path": Path("/home/acme/.config/fish/config.fish"),
}

_SAMPLES: dict[type[BaseModel], BaseModel] = {
    Package: Package(source="dotfiles", name="fish", on=True, policy="sync", items=1),
    PackageDetail: PackageDetail(
        source="dotfiles",
        name="fish",
        on=True,
        items=(ItemDetail(name="config", enabled=True),),
    ),
    EntryStatus: EntryStatus(
        source="dotfiles",
        package="fish",
        item="config",
        entry="~/.config/fish/config.fish",
        mode="link",
        policy="manual",
        state="applied",
        target_path=Path("/home/acme/.config/fish/config.fish"),
    ),
    StatusSummary: StatusSummary(
        checked_at=datetime(2026, 10, 2, tzinfo=UTC), total=1, attention=0
    ),
    ApplyOutcome: ApplyOutcome(**_PLACE),  # type: ignore[arg-type]
}

_PLACE_KEYS = [
    "source",
    "package",
    "item",
    "kind",
    "entry",
    "mode",
    "action",
    "state",
    "detail",
    "diff",
    "target_path",
]

#: The keys of each row, in order (``StatusSummary`` is ``status.json``, read by prompts).
_ROW_KEYS: dict[type[BaseModel], list[str]] = {
    Package: [
        "source",
        "name",
        "on",
        "policy",
        "suggested",
        "chosen",
        "items",
        "description",
        "excluded",
        "error",
    ],
    PackageDetail: [
        "source",
        "name",
        "on",
        "policy",
        "suggested",
        "chosen",
        "description",
        "excluded",
        "inputs",
        "items",
        "error",
    ],
    EntryStatus: [
        "source",
        "package",
        "item",
        "kind",
        "entry",
        "mode",
        "policy",
        "chosen",
        "state",
        "detail",
        "target_path",
        "error",
    ],
    StatusSummary: [
        "checked_at",
        "total",
        "apply",
        "attention",
        "pending",
        "foreign",
        "new",
        "applied",
        "behind",
        "modified",
        "conflict",
        "missing",
        "orphan",
        "off",
        "error",
    ],
    ApplyOutcome: _PLACE_KEYS,
}

_KINDS = {
    Package: "dotfiles.package",
    PackageDetail: "dotfiles.package_detail",
    EntryStatus: "dotfiles.entry_status",
    ApplyOutcome: "dotfiles.apply_outcome",
}


def test_each_record_a_command_prints_has_the_kind_of_its_class_name() -> None:
    installed = install(Dotfiles)
    for model, kind in _KINDS.items():
        ran = installed.cli(["plugin", "schema", kind])
        assert ran.exit == 0, ran.stderr
        schema = json.loads(ran.stdout)
        assert schema["$id"] == kind
        assert set(schema["properties"]) == set(model.model_fields)


@pytest.mark.parametrize("model", list(_ROW_KEYS), ids=lambda model: model.__name__)
def test_a_record_writes_its_row_keys_in_order(model: type[BaseModel]) -> None:
    assert list(json.loads(_SAMPLES[model].model_dump_json())) == _ROW_KEYS[model]


def test_a_failed_place_row_adds_its_error_last() -> None:
    error = Failure.from_exception(UntapedError("could not read"))
    row = ApplyOutcome(**{**_PLACE, "action": "failed"}, error=error)  # type: ignore[arg-type]

    assert list(json.loads(row.model_dump_json())) == [*_PLACE_KEYS, "error"]
