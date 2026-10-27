"""A plugin's private file in a store repo: ``read_private`` and ``write_private``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from git_test_support import StoreError, StoreFor
from untaped.sdk import Absent, ErrorCategory, Found, OtherVersion, Unreadable


class Listing(BaseModel):
    repo: str
    archived: bool = False


def _file(store_for: StoreFor, plugin: str) -> Path:
    return store_for(plugin).path / f"untaped-{plugin}.json"


def test_a_typed_value_round_trips_with_its_freshness(store_for: StoreFor) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    refreshed = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)

    assert store.read_private(Listing) == Absent()
    store.write_private(Listing(repo="acme/app"), refreshed_at=refreshed)

    found = store.read_private(Listing)
    assert isinstance(found, Found)
    assert (found.value, found.refreshed_at) == (Listing(repo="acme/app"), refreshed)
    data = json.loads(_file(store_for, "github").read_text(encoding="utf-8"))
    assert data["repo"] == "acme/app"  # at the top level, where 11.0 read it
    assert _file(store_for, "github").stat().st_mode & 0o777 == 0o600


def test_an_11_0_file_with_no_stamp_reads_in_its_shape(store_for: StoreFor) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    _file(store_for, "github").write_text('{"repo": "acme/app", "archived": true}\n')

    assert store.read_private(Listing) == Found(Listing(repo="acme/app", archived=True))


@pytest.mark.parametrize(
    ("text", "kind"),
    [("{not json", Unreadable), ('{"untaped": {"file": 99, "by": "99.0.0"}}', OtherVersion)],
)
def test_a_file_it_cannot_read_is_never_absent(
    store_for: StoreFor, text: str, kind: type[object]
) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    _file(store_for, "github").write_text(text)

    assert isinstance(store.read_private(Listing), kind)


def test_a_presence_mark_keeps_a_file_already_there(store_for: StoreFor) -> None:
    store = store_for("ansible")
    store.fetch(branches=["main"])
    store.write_private()
    assert set(json.loads(_file(store_for, "ansible").read_text())) == {"untaped"}
    assert store.has_part()

    _file(store_for, "ansible").write_text('{"kept": true}\n')
    store.write_private()

    assert _file(store_for, "ansible").read_text() == '{"kept": true}\n'


def test_writing_needs_the_repo(store_for: StoreFor) -> None:
    with pytest.raises(StoreError, match="no repo store repository") as raised:
        store_for("github").write_private()
    assert raised.value.category == ErrorCategory.FAILED


def test_a_failed_write_is_the_plugins_error(store_for: StoreFor) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    _file(store_for, "github").mkdir()  # a directory where the file goes

    with pytest.raises(StoreError, match="could not write") as raised:
        store.write_private(Listing(repo="acme/app"))
    assert (raised.value.category, raised.value.system) == (ErrorCategory.FAILED, "local")


def test_freshness_needs_the_value_it_dates(store_for: StoreFor) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    with pytest.raises(TypeError, match="needs the value"):
        store.write_private(refreshed_at=datetime(2026, 10, 21, tzinfo=UTC))
