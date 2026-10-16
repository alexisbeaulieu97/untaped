"""Each dotfiles record declares its kind and keeps the keys, in order, of the rows it replaced."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.sdk import Failure, UntapedError, kind_of, record_model
from untaped_dotfiles.records import (
    ApplyOutcome,
    ItemOutcome,
    ItemRow,
    PlaceOutcome,
    RemoveOutcome,
    RepoOutcome,
    RepoRow,
    StatusRow,
    StatusSummary,
    SyncOutcome,
)

_PLACE = {
    "repo": "dotfiles",
    "item": "fish",
    "source": "fish/config.fish",
    "mode": "link",
    "action": "created",
    "state": "pending",
    "target_path": Path("/home/acme/.config/fish/config.fish"),
}

_SAMPLES: dict[type[BaseModel], BaseModel] = {
    ItemRow: ItemRow(repo="dotfiles", name="fish", suggested="manual", files=2),
    ItemOutcome: ItemOutcome(repo="dotfiles", name="fish", action="created", policy="sync"),
    RepoRow: RepoRow(
        name="dotfiles",
        url="https://example.com/acme/dotfiles.git",
        ref="main",
        managed=True,
        target_path=Path("/home/acme/repos/dotfiles"),
    ),
    RepoOutcome: RepoOutcome(name="dotfiles", action="deleted", detail="kept"),
    StatusRow: StatusRow(
        repo="dotfiles",
        item="fish",
        file="fish/config.fish",
        source="fish/config.fish",
        mode="link",
        policy="manual",
        state="applied",
        target_path=Path("/home/acme/.config/fish/config.fish"),
    ),
    StatusSummary: StatusSummary(
        checked_at=datetime(2026, 10, 2, tzinfo=UTC), total=1, attention=0
    ),
    ApplyOutcome: ApplyOutcome(**_PLACE),
    SyncOutcome: SyncOutcome(**_PLACE),
    RemoveOutcome: RemoveOutcome(**_PLACE),
}

_PLACE_KEYS = ["repo", "item", "source", "mode", "action", "state", "detail", "target_path"]

#: The keys, in order, each command wrote before its rows declared a kind.
_ROW_KEYS: dict[type[BaseModel], list[str]] = {
    ItemRow: [
        "repo",
        "name",
        "suggested",
        "policy",
        "enabled",
        "files",
        "skip",
        "excluded",
        "description",
    ],
    ItemOutcome: ["repo", "name", "action", "policy", "detail"],
    RepoRow: ["name", "url", "ref", "managed", "head", "behind", "items", "target_path"],
    RepoOutcome: ["name", "action", "detail"],
    StatusRow: [
        "repo",
        "item",
        "file",
        "source",
        "mode",
        "policy",
        "state",
        "detail",
        "target_path",
    ],
    StatusSummary: [
        "checked_at",
        "total",
        "attention",
        "pending",
        "foreign",
        "applied",
        "behind",
        "modified",
        "conflict",
        "missing",
        "orphan",
        "error",
        "repos_behind",
    ],
    ApplyOutcome: _PLACE_KEYS,
    SyncOutcome: _PLACE_KEYS,
    RemoveOutcome: _PLACE_KEYS,
}

_KINDS = {
    ItemRow: "dotfiles.item",
    ItemOutcome: "dotfiles.item_outcome",
    RepoRow: "dotfiles.repo",
    RepoOutcome: "dotfiles.repo_outcome",
    StatusRow: "dotfiles.status",
    StatusSummary: "dotfiles.status.summary",
    ApplyOutcome: "dotfiles.apply_outcome",
    SyncOutcome: "dotfiles.sync_outcome",
    RemoveOutcome: "dotfiles.remove_outcome",
}


@pytest.mark.parametrize("model", list(_KINDS), ids=lambda model: model.__name__)
def test_a_record_declares_its_wire_kind(model: type[BaseModel]) -> None:
    assert kind_of(model) == _KINDS[model]
    assert record_model(_KINDS[model]) is model


@pytest.mark.parametrize("model", list(_ROW_KEYS), ids=lambda model: model.__name__)
def test_a_record_writes_its_old_row_keys_in_their_order(model: type[BaseModel]) -> None:
    assert list(json.loads(_SAMPLES[model].model_dump_json())) == _ROW_KEYS[model]


def test_the_applier_row_is_kind_less_so_each_command_names_its_own() -> None:
    assert kind_of(PlaceOutcome) is None


def test_a_failed_place_row_adds_its_error_last() -> None:
    error = Failure.from_exception(UntapedError("could not read"))
    row = ApplyOutcome(**{**_PLACE, "action": "failed"}, error=error)

    assert list(json.loads(row.model_dump_json())) == [*_PLACE_KEYS[:-1], "error", "target_path"]
