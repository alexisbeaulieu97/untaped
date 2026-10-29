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
    Item,
    ItemOutcome,
    PathStatus,
    RemoveOutcome,
    Repo,
    RepoOutcome,
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
    Item: Item(repo="dotfiles", name="fish", suggested="manual", files=2),
    ItemOutcome: ItemOutcome(repo="dotfiles", name="fish", action="created", policy="sync"),
    Repo: Repo(
        name="dotfiles",
        url="https://example.com/acme/dotfiles.git",
        ref="main",
        managed=True,
        target_path=Path("/home/acme/repos/dotfiles"),
    ),
    RepoOutcome: RepoOutcome(name="dotfiles", action="deleted", detail="kept"),
    PathStatus: PathStatus(
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
    Item: [
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
    Repo: ["name", "url", "ref", "managed", "head", "behind", "items", "target_path"],
    RepoOutcome: ["name", "action", "detail"],
    PathStatus: [
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
    Item: "dotfiles.item",
    ItemOutcome: "dotfiles.item_outcome",
    Repo: "dotfiles.repo",
    RepoOutcome: "dotfiles.repo_outcome",
    PathStatus: "dotfiles.path_status",
    StatusSummary: "dotfiles.status_summary",
    ApplyOutcome: "dotfiles.apply_outcome",
    SyncOutcome: "dotfiles.sync_outcome",
    RemoveOutcome: "dotfiles.remove_outcome",
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
def test_a_record_writes_its_old_row_keys_in_their_order(model: type[BaseModel]) -> None:
    assert list(json.loads(_SAMPLES[model].model_dump_json())) == _ROW_KEYS[model]


def test_a_failed_place_row_adds_its_error_last() -> None:
    error = Failure.from_exception(UntapedError("could not read"))
    row = ApplyOutcome(**{**_PLACE, "action": "failed"}, error=error)

    assert list(json.loads(row.model_dump_json())) == [*_PLACE_KEYS, "error"]
