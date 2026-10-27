"""StateWorkspaceStore against the plugin's rows in the hermetic home."""

from __future__ import annotations

import contextvars
import threading
from datetime import UTC, datetime

import pytest

from untaped.sdk import ConfigError, StateRows
from untaped.sdk.testing import read_rows, write_rows
from untaped_workspace.domain.models import RepoSpec, WorkspaceRecord
from untaped_workspace.domain.naming import workspace_key
from untaped_workspace.errors import WorkspaceError, WorkspaceNotFoundError
from untaped_workspace.infrastructure import StateWorkspaceStore
from untaped_workspace.infrastructure.state_store import ARCHIVED, CHECKOUTS, WORKSPACES

T0 = datetime(2026, 10, 1, tzinfo=UTC)
SPEC = RepoSpec(
    url="https://github.com/acme/api.git", name="acme/api", dir="api", branch="b", base="main"
)


def test_create_get_add_archive() -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    store.add_repos("w", [SPEC])
    assert store.get("w") == WorkspaceRecord(name="w", created_at=T0, repos=(SPEC,))
    archived = store.archive("w", at=T0)
    assert archived.archived_at == T0
    assert store.get("w") is None
    assert [a.name for a in store.archived()] == ["w"]


def test_create_twice_is_a_conflict() -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    with pytest.raises(WorkspaceError) as caught:
        store.create(WorkspaceRecord(name="w", created_at=T0))
    assert caught.value.category == "conflict"
    assert caught.value.hint == "run `untaped workspace add w --repo REPO`"


def test_a_name_can_be_archived_twice() -> None:
    store = StateWorkspaceStore()
    for _ in range(2):
        store.create(WorkspaceRecord(name="w", created_at=T0))
        store.archive("w", at=T0)
    assert len(store.archived()) == 2


def test_add_to_unknown_workspace() -> None:
    with pytest.raises(WorkspaceNotFoundError):
        StateWorkspaceStore().add_repos("nope", [SPEC])


def test_add_repos_twice_records_once() -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    store.add_repos("w", [SPEC])
    record = store.add_repos("w", [SPEC])
    assert record.repos == (SPEC,)
    stored = store.get("w")
    assert stored is not None and stored.repos == (SPEC,)


def test_one_repo_under_two_url_forms_records_once() -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    https = SPEC.model_copy(update={"url": "https://github.com/acme/api.git"})
    ssh = SPEC.model_copy(update={"url": "git@github.com:acme/api.git", "dir": "acme-api"})
    store.add_repos("w", [https])
    assert store.add_repos("w", [ssh]).repos == (https,)


def test_a_held_workspace_lock_makes_that_workspace_busy_and_no_other() -> None:
    held: dict[str, bool] = {}

    def probe(name: str) -> None:  # another thread: within one thread the lock is re-entrant
        with CHECKOUTS.try_locked(workspace_key(name)) as got:
            held[name] = got

    with StateWorkspaceStore().locked("w"):
        for name in ("w", "v"):
            worker = threading.Thread(target=contextvars.copy_context().run, args=(probe, name))
            worker.start()
            worker.join()
        with StateWorkspaceStore().locked("w"):  # again in this thread: it only counts
            pass
    assert held == {"w": False, "v": True}
    with CHECKOUTS.try_locked(workspace_key("w")) as free:
        assert free


def test_archive_keeps_the_history_record_when_removing_the_active_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    store.add_repos("w", [SPEC])

    def fail(self: StateRows[WorkspaceRecord], key: str) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(StateRows, "delete", fail)
    with pytest.raises(OSError):
        store.archive("w", at=T0)
    [archived] = store.archived()
    assert (archived.name, archived.repos) == ("w", (SPEC,))
    assert store.get("w") is not None  # a recoverable duplicate, not a loss


def test_remove_drops_every_record_of_the_name() -> None:
    store = StateWorkspaceStore()
    for name in ("w", "w", "other"):
        store.create(WorkspaceRecord(name=name, created_at=T0))
        store.archive(name, at=T0)
    store.create(WorkspaceRecord(name="w", created_at=T0))
    store.add_repos("w", [SPEC])

    store.remove("w")

    assert store.get("w") is None
    assert [record.name for record in store.archived()] == ["other"]
    store.remove("w")  # nothing left of the name: a no-op
    assert [record.name for record in store.archived()] == ["other"]


def test_a_workspace_made_before_11_from_a_url_untaped_now_refuses_still_loads() -> None:
    """10.x took local paths and ``http://`` URLs; such a workspace still loads and removes."""
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    store.create(WorkspaceRecord(name="other", created_at=T0))
    store.add_repos("other", [SPEC])
    rows = read_rows(WORKSPACES)
    rows[1]["repos"][0]["url"] = "/srv/git/api.git"
    write_rows(WORKSPACES, rows)
    store.add_repos(
        "w",
        [SPEC.model_copy(update={"url": "http://git.example/acme/web.git", "dir": "web"})],
    )

    assert [spec.url for spec in store.get("other").repos] == ["/srv/git/api.git"]  # type: ignore[union-attr]
    assert [spec.url for spec in store.get("w").repos] == ["http://git.example/acme/web.git"]  # type: ignore[union-attr]
    store.remove("other")
    assert [record.name for record in store.active()] == ["w"]


def test_a_newer_installs_fields_survive_adding_a_repo() -> None:
    write_rows(
        WORKSPACES,
        [
            {
                "name": "w",
                "created_at": "2026-10-01T00:00:00Z",
                "pinned": True,
                "repos": [{**SPEC.model_dump(mode="json"), "sparse": ["docs"]}],
            }
        ],
    )
    web = SPEC.model_copy(update={"url": "https://github.com/acme/web.git", "dir": "web"})

    StateWorkspaceStore().add_repos("w", [web])

    (row,) = read_rows(WORKSPACES)
    assert row["pinned"] is True
    assert row["repos"][0]["sparse"] == ["docs"]
    assert row["repos"][1] == web.model_dump(mode="json")


def test_a_damaged_active_record_names_its_row() -> None:
    write_rows(WORKSPACES, [{"name": "w"}])

    with pytest.raises(ConfigError, match=r"workspace\.workspaces row w .* is damaged") as caught:
        StateWorkspaceStore().active()

    assert caught.value.exit_code == 4
    assert caught.value.hint == "correct or remove that row by hand"


def test_names_still_lists_a_damaged_record() -> None:
    write_rows(WORKSPACES, [{"name": "w"}, {"name": "v", "created_at": "2026-10-01T00:00:00Z"}])

    assert StateWorkspaceStore().names() == ["w", "v"]


def test_a_newer_installs_fields_on_an_archived_record_are_ignored() -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    store.add_repos("w", [SPEC])
    store.archive("w", at=T0)
    rows = read_rows(ARCHIVED)
    rows[0]["repos"][0]["sparse"] = ["docs"]
    write_rows(ARCHIVED, rows)

    [archived] = store.archived()

    assert (archived.name, archived.repos) == ("w", (SPEC,))
