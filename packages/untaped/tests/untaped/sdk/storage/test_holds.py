"""Holds: a project holds an item an owner declared, and core releases it through the owner.

A fake owner stands in for workspace: it keeps an item while another holder
holds it and deletes it otherwise, refuses items with "unpushed work" unless
forced, and can be told to raise while planning or applying.
"""

from __future__ import annotations

import contextvars
import json
import os
import shutil
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import RootModel, ValidationError, field_validator

from untaped.core.project_lifecycle import Removal, apply_removal, plan_removal
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginSpec
from untaped.sdk.errors import ConfigError, UntapedError
from untaped.sdk.projects import Project, holder_of, known_projects
from untaped.sdk.storage import _refs as refs
from untaped.sdk.storage import holds
from untaped.sdk.storage._refs import PROJECT, RefHolder, RefTarget
from untaped.sdk.storage.holds import (
    HolderKind,
    HolderView,
    ItemStore,
    LetGo,
    MoveAsk,
    Refused,
    ReleaseAsk,
    Sighting,
    UnknownHolder,
    plan_holds,
)
from untaped.sdk.testing import (
    CliResult,
    ScriptedPromptBackend,
    invoke_cli,
    make_project,
    move_ask,
    plan_release,
    plugin_candidate,
    release_ask,
    write_reference,
)

pytestmark = pytest.mark.usefixtures("home_run")


def _in_this_run(target: Callable[[], object]) -> threading.Thread:
    """A thread under this test's run, as a command's workers run (``bounded_map`` copies it)."""
    return threading.Thread(target=contextvars.copy_context().run, args=(target,))


class RepoKey(RootModel[str]):
    """``github.com/acme/api``: lower case, no ``.git``."""

    @field_validator("root")
    @classmethod
    def _normal(cls, value: str) -> str:
        return value.lower().removesuffix(".git")


@dataclass
class Owner:
    """What the fake owner does, and what it was asked."""

    refuse: set[str] = field(default_factory=set)
    raise_plan: set[str] = field(default_factory=set)
    raise_apply: set[str] = field(default_factory=set)
    raise_moved: int = 0
    fail_moved: set[str] = field(default_factory=set)
    deleted: list[str] = field(default_factory=list)
    fresh: list[ReleaseAsk[Any]] = field(default_factory=list)
    moves: list[MoveAsk[Any]] = field(default_factory=list)
    during_apply: list[Any] = field(default_factory=list)
    during_moved: list[Any] = field(default_factory=list)


OWNER = Owner()


def _release(ask: ReleaseAsk[RepoKey]) -> LetGo[RepoKey] | Refused:
    item = ask.item.root
    if item in OWNER.raise_plan:
        raise RuntimeError("the plan broke")
    if item in OWNER.refuse and not ask.force:
        return Refused(reason=f"{item}: 2 commits never pushed", hint="push them")
    if ask.others:
        return LetGo(preview="keeps the repo: another project holds it", apply=_keep)
    return LetGo(preview="deletes the repo", apply=_delete)


def _keep(fresh: ReleaseAsk[RepoKey]) -> str | Refused:
    OWNER.fresh.append(fresh)
    for step in OWNER.during_apply:
        step()
    if fresh.item.root in OWNER.raise_apply:
        raise RuntimeError("the apply broke")
    if not fresh.others:
        return Refused(reason="now the last holder", hint="run remove again to delete it")
    return "kept the repo"


def _delete(fresh: ReleaseAsk[RepoKey]) -> str | Refused:
    OWNER.fresh.append(fresh)
    for step in OWNER.during_apply:
        step()
    if fresh.item.root in OWNER.raise_apply:
        raise RuntimeError("the apply broke")
    if fresh.others:
        return "kept the repo: a holder arrived since the preview"  # it may do less, never more
    OWNER.deleted.append(fresh.item.root)
    return "deleted the repo"


def _moved(ask: MoveAsk[RepoKey]) -> str | None:
    OWNER.moves.append(ask)
    for step in OWNER.during_moved:
        step()
    if OWNER.raise_moved or ask.item.root in OWNER.fail_moved:
        OWNER.raise_moved = max(OWNER.raise_moved - 1, 0)
        raise RuntimeError("worktree repair failed")
    return f"repaired worktree {ask.item.root.rsplit('/', 1)[-1]}"


REPOS = ItemStore("acme", "repos", item=RepoKey, release=_release, moved=_moved)
SPEC = PluginSpec(name="acme", stores=(REPOS,))
API, WEB = "github.com/acme/api", "github.com/acme/web"


@pytest.fixture(autouse=True)
def _owner() -> Iterator[None]:
    global OWNER
    OWNER = Owner()
    with bootstrap.kept_composition(quiet=True):
        bootstrap.compose_root(candidates=[plugin_candidate(SPEC)])
        yield


def _cli(*args: str, specs: tuple[PluginSpec, ...] = (SPEC,)) -> CliResult:
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec) for spec in specs])
    return invoke_cli(root.meta, list(args))


def _rows(result: CliResult) -> list[dict[str, Any]]:
    assert result.stdout, result.output
    return json.loads(result.stdout)


def _targets(project: Project) -> tuple[RefTarget, ...]:
    return refs.read_reference(refs.reference_file(holder_of(project))).targets


def _two(tmp_path: Path) -> tuple[Project, Project]:
    """PROJ-123 holds api and web; PROJ-200 holds api."""
    first = make_project(tmp_path / "PROJ-123", holds={REPOS: [API, WEB]})
    second = make_project(tmp_path / "PROJ-200", holds={REPOS: [API]})
    return first, second


# --- declaring and holding --------------------------------------------------------------


def test_an_item_store_says_how_items_are_released() -> None:
    with pytest.raises(TypeError):
        ItemStore("acme", "repos", release=_release)  # type: ignore[call-arg]  # moved= is required
    with pytest.raises(ConfigError, match="one settings segment"):
        ItemStore("acme", "Repos", release=_release, moved=None)
    with pytest.raises(ConfigError, match="item must be str or a RootModel"):
        ItemStore("acme", "repos", item=int, release=_release, moved=None)  # type: ignore[type-var]
    same = ItemStore("acme", "repos", item=RepoKey, release=_release, moved=_moved)
    assert same == REPOS and hash(same) == hash(REPOS)
    assert REPOS.target("GitHub.com/acme/API.git") == RefTarget(
        plugin="acme", store="repos", item=API
    )


def test_holding_is_idempotent_and_normalised_by_the_item_type(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    project.hold(REPOS, RepoKey("github.com/acme/api.git"))
    project.hold(REPOS, "GitHub.com/acme/api")
    project.hold(REPOS, RepoKey(API))
    assert project.holds(REPOS) == (RepoKey(API),)
    assert _targets(project) == (REPOS.target(API),)


def test_holding_an_item_store_no_composed_plugin_lists_is_refused(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    stray = ItemStore("acme", "other", release=_release, moved=None)
    with pytest.raises(ConfigError, match=r"acme\.other is not an item store a composed plugin"):
        project.hold(stray, "x")
    assert _targets(project) == ()


def test_item_stores_share_the_stores_listing_and_its_checks() -> None:
    twice = PluginSpec(name="acme", stores=(REPOS, REPOS))
    foreign = PluginSpec(name="other", stores=(REPOS,))
    with bootstrap.kept_composition(quiet=True):
        result = bootstrap.compose_root(
            candidates=[plugin_candidate(twice), plugin_candidate(foreign)]
        )
    assert {record.reason for record in result.quarantine} == {"duplicate-store", "foreign-store"}
    assert refs.composed_targets() == ()  # an item store keeps no layout an install holds


def test_holders_lists_every_holder_and_every_reference_it_cannot_read(tmp_path: Path) -> None:
    first, second = _two(tmp_path)
    (refs.REFS.place / "project-ffffffffffff.json").write_text("{", encoding="utf-8")
    seen = REPOS.holders(API)
    views = [h for h in seen if isinstance(h, HolderView)]
    assert {h.holder.name for h in views} == {first.id, second.id}
    assert all(h.state == "present" for h in views)
    assert [h.label for h in seen if isinstance(h, UnknownHolder)] == [
        "~/.untaped/plugins/untaped/refs/project-ffffffffffff.json "
        "(unreadable (it is not valid JSON))"
    ]
    assert {h.holder.name for h in REPOS.holders(WEB) if isinstance(h, HolderView)} == {first.id}


# --- the asks -----------------------------------------------------------------------------


def test_the_asks_check_their_fields() -> None:
    ask = release_ask(REPOS, API, others=["present", "missing"])
    assert ask.item == RepoKey(API) and [o.state for o in ask.others] == ["present", "missing"]
    with pytest.raises(ValidationError, match="unreadable releases nothing"):
        release_ask(REPOS, API, holder="unreadable")
    with pytest.raises(ValidationError, match="not one of the others"):
        ReleaseAsk(item=RepoKey(API), holder=ask.holder, others=(ask.holder,), force=False)
    with pytest.raises(ValidationError, match="moved_from is where the holder is now"):
        MoveAsk(item=RepoKey(API), holder=ask.holder, others=(), moved_from=ask.holder.anchor)
    plan = _release(ask)
    assert isinstance(plan, LetGo) and plan.model_dump() == {"preview": plan.preview}
    assert "apply" not in repr(plan)


def test_plan_release_opens_a_run_when_a_plugin_test_has_none(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    plans = contextvars.Context().run(plan_release, first, REPOS)  # no run in this context
    assert len(plans) == 2


def test_testing_helpers_plan_without_applying(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    plans = plan_release(first, REPOS)
    assert [p.preview for p in plans if isinstance(p, LetGo)] == [
        "keeps the repo: another project holds it",
        "deletes the repo",
    ]
    assert OWNER.fresh == [] and OWNER.deleted == []
    assert _moved(move_ask(REPOS, API, moved_from=Path("/old"))) == "repaired worktree api"
    lonely = release_ask(REPOS, API)
    assert isinstance(_release(lonely), LetGo) and _release(lonely).preview == "deletes the repo"


# --- remove -------------------------------------------------------------------------------


def test_remove_releases_each_hold_then_deletes_only_what_core_wrote(tmp_path: Path) -> None:
    first, second = _two(tmp_path)
    folder = first.root / ".untaped"
    (folder / "config.yml").write_text("awx: {}\n", encoding="utf-8")
    (folder / "lint.yml").write_text("version: 1\n", encoding="utf-8")
    (folder / "state.yml").write_text("format_version: 1\n", encoding="utf-8")
    (folder / "plugins" / "acme").mkdir(parents=True)
    (first.root / "notes.md").write_text("mine\n", encoding="utf-8")
    result = _cli("project", "remove", str(first.root), "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    rows = _rows(result)
    assert [(r["item"], r["action"], r["detail"]) for r in rows] == [
        (API, "released", "kept the repo"),
        (WEB, "released", "deleted the repo"),
        (
            str(first.root),
            "removed",
            f"removed the project; kept {first.root}: config.yml, lint.yml, notes.md",
        ),
    ]
    assert OWNER.deleted == [WEB]
    assert sorted(p.name for p in folder.iterdir()) == ["config.yml", "lint.yml"]
    assert (first.root / "notes.md").read_text(encoding="utf-8") == "mine\n"
    assert not refs.reference_file(holder_of(first)).exists()
    assert not (refs.REFS.place / f".project-{first.id}.lock").exists()
    assert [h.holder.name for h in REPOS.holders(API) if isinstance(h, HolderView)] == [second.id]


def test_remove_of_an_empty_project_removes_the_folder_too(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    result = _cli("project", "remove", str(project.root), "--yes", "--format", "json")
    assert result.exit_code == 0, result.output
    [row] = _rows(result)
    assert row["action"] == "removed" and "which held nothing else" in row["detail"]
    assert not project.root.exists()


def test_one_refusal_applies_nothing(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    OWNER.refuse = {WEB}
    result = _cli("project", "remove", str(first.root), "--yes", "--format", "json")
    assert result.exit_code == 1
    rows = {r["item"]: r for r in _rows(result)}
    assert rows[API]["action"] == "planned"
    assert rows[WEB]["action"] == "refused"
    assert rows[WEB]["hint"] == f"push them, or --force {WEB}"
    assert "1 hold refused; nothing removed" in result.stderr
    assert f"--force {WEB}" in result.stderr
    assert OWNER.fresh == [] and len(_targets(first)) == 2 and first.root.exists()


def test_force_names_the_items_whose_refusal_is_overridden(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    OWNER.refuse = {WEB}
    dry = _cli("project", "remove", str(first.root), "--force", WEB, "--dry-run", "-f", "json")
    assert dry.exit_code == 0
    assert {r["item"]: r["action"] for r in _rows(dry)}[WEB] == "planned"
    result = _cli("project", "remove", str(first.root), "--force", WEB, "--yes", "-f", "json")
    assert result.exit_code == 0, result.output
    assert OWNER.deleted == [WEB]
    assert [ask.force for ask in OWNER.fresh] == [False, True]


def test_dry_run_and_check_preview_without_applying(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    dry = _cli("project", "remove", str(first.root), "--dry-run", "-f", "json")
    assert dry.exit_code == 0
    assert [r["action"] for r in _rows(dry)] == ["planned", "planned", "planned"]
    assert _rows(dry)[-1]["detail"].startswith("deletes .gitignore, project.json")
    clean = _cli("project", "remove", str(first.root), "--check", "-f", "json")
    assert clean.exit_code == 0
    OWNER.refuse = {API}
    assert _cli("project", "remove", str(first.root), "--check").exit_code == 3
    assert OWNER.fresh == [] and len(_targets(first)) == 2


def test_a_plan_that_raises_is_a_failed_row_and_nothing_applies(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    OWNER.raise_plan = {API}
    result = _cli("project", "remove", str(first.root), "--yes", "-f", "json")
    assert result.exit_code == 1
    row = next(r for r in _rows(result) if r["item"] == API)
    assert (row["action"], row["detail"]) == ("failed", "raised RuntimeError: the plan broke")
    assert OWNER.fresh == [] and len(_targets(first)) == 2


def test_an_apply_that_raises_keeps_its_hold_and_the_rest_still_run(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    OWNER.raise_apply = {API}
    result = _cli("project", "remove", str(first.root), "--yes", "-f", "json")
    assert result.exit_code == 1
    rows = {r["item"]: r for r in _rows(result)}
    assert rows[API]["action"] == "failed"
    assert rows[WEB]["action"] == "released"
    assert rows[str(first.root)]["action"] == "skipped"
    assert _targets(first) == (REPOS.target(API),)
    assert (first.root / ".untaped" / "project.json").exists()


def test_a_holder_added_since_the_preview_is_in_the_fresh_ask(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ-123", holds={REPOS: [WEB]})
    late = make_project(tmp_path / "PROJ-300")
    with first.locked():
        removal = plan_removal(first, force=())
        assert removal.plans[0].answer.preview == "deletes the repo"  # type: ignore[union-attr]
        late.hold(REPOS, WEB)
        rows = apply_removal(removal)
    [fresh] = OWNER.fresh
    assert [o.holder.name for o in fresh.others if isinstance(o, HolderView)] == [late.id]
    assert rows[0].detail == "kept the repo: a holder arrived since the preview"
    assert OWNER.deleted == []


def test_two_removes_of_a_co_held_item_never_leave_it_without_a_holder(tmp_path: Path) -> None:
    first, second = _two(tmp_path)
    with first.locked():
        mine = plan_removal(first, force=())  # previewed: PROJ-200 holds api, so keep it
        with second.locked():
            theirs = apply_removal(plan_removal(second, force=()))  # it keeps api too: we hold it
        assert [r.action for r in theirs] == ["released", "removed"]
        rows = apply_removal(mine)
    by_item = {r.item: r for r in rows}
    assert by_item[API].action == "refused"
    assert by_item[API].detail == "changed since the preview: now the last holder"
    assert by_item[API].hint == "run remove again to delete it"
    assert by_item[WEB].action == "released"
    assert by_item[str(first.root)].action == "skipped"
    assert _targets(first) == (REPOS.target(API),)  # the hold stays with the last holder
    again = _cli("project", "remove", str(first.root), "--yes", "-f", "json")
    assert again.exit_code == 0, again.output
    assert OWNER.deleted == [WEB, API]


def test_the_item_store_lock_is_taken_per_item_and_times_out_with_exit_5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = _two(tmp_path)
    lock = refs.REFS.place / ".acme-repos.lock"
    held: list[bool] = []

    def probe() -> None:  # from another thread: is the store's lock free right now?
        def take() -> None:
            try:
                with holds.store_lock(REPOS):
                    held.append(False)
            except ConfigError:
                held.append(True)

        thread = _in_this_run(take)
        thread.start()
        thread.join()

    monkeypatch.setattr(holds, "LOCK_WAIT", 0.0)
    OWNER.during_apply = [probe]
    with first.locked():
        removal = plan_removal(first, force=())
        holds_between: list[bool] = []
        original = holds.drop_hold

        def drop(holder: RefHolder, target: RefTarget) -> None:
            original(holder, target)
            probe()  # between the drop and the next item: still under this item's lock
            holds_between.append(held.pop())

        monkeypatch.setattr(holds, "drop_hold", drop)
        apply_removal(removal)
    assert held == [True, True]  # each apply runs under the store's lock
    assert holds_between == [True, True]
    probe()
    assert held[-1] is False  # and it is let go after each item
    assert lock.exists()

    other = make_project(tmp_path / "PROJ-9", holds={REPOS: ["github.com/acme/x"]})
    stop = threading.Event()
    taken = threading.Event()

    def hog() -> None:
        with holds.store_lock(REPOS):
            taken.set()
            stop.wait(5)

    thread = _in_this_run(hog)
    thread.start()
    taken.wait(5)
    try:
        result = _cli("project", "remove", str(other.root), "--yes")
    finally:
        stop.set()
        thread.join()
    assert result.exit_code == 5
    assert "another untaped is changing the items of acme.repos; try again" in result.stderr


def test_a_reference_that_cannot_be_read_counts_as_another_holder(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ-123", holds={REPOS: [WEB]})
    write_reference(RefHolder(kind=PROJECT, name="ffffffffffff"), [], anchor=Path("/x"))  # opaque
    result = _cli("project", "remove", str(first.root), "--yes", "-f", "json")
    assert result.exit_code == 0, result.output
    assert _rows(result)[0]["detail"] == "kept the repo"  # an unknown holder keeps the item
    assert OWNER.deleted == []


def test_an_uninstalled_owners_hold_is_forgotten_only_by_name(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ-500", holds={REPOS: ["pack:terraform"]})
    gone = _cli("project", "remove", str(first.root), "--dry-run", "-f", "json", specs=())
    [row, _] = _rows(gone)
    assert (row["action"], row["detail"]) == ("refused", "acme is not installed: the hold stays")
    assert row["hint"] == "install acme, or --force pack:terraform forgets it"
    forced = _cli(
        "project",
        "remove",
        str(first.root),
        "--force",
        "pack:terraform",
        "--yes",
        "-f",
        "json",
        specs=(),
    )
    assert forced.exit_code == 0, forced.output
    assert [r["action"] for r in _rows(forced)] == ["forgotten", "removed"]


def test_a_quarantined_owners_hold_is_never_forgotten(tmp_path: Path) -> None:
    broken = PluginSpec(
        name="acme", stores=(REPOS, ItemStore("acme", "repos", release=_release, moved=None))
    )
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    result = _cli(
        "project", "remove", str(first.root), "--force", API, "--yes", "-f", "json", specs=(broken,)
    )
    assert result.exit_code == 1
    row = _rows(result)[0]
    assert row["action"] == "refused" and row["detail"].startswith("acme is broken")
    assert "hint" not in row
    assert _targets(first) == (REPOS.target(API),)


def test_remove_needs_yes_without_a_terminal(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    result = _cli("project", "remove", str(first.root))
    assert result.exit_code == 2
    assert (
        f'untaped project remove requires --yes when not interactive: it asks "Remove project '
        f'{first.root.name}?"'
    ) in result.stderr
    assert len(_targets(first)) == 2


def test_remove_at_a_terminal_previews_the_holds_as_the_rows_print_them(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    root = bootstrap.build_root_app(candidates=[plugin_candidate(SPEC)])
    result = invoke_cli(
        root.meta,
        ["project", "remove", str(first.root)],
        terminal=True,
        prompt_backend=ScriptedPromptBackend(confirms=[True]),
    )
    assert result.exit_code == 0, result.output
    header = next(line for line in result.stderr.splitlines() if "plugin" in line)
    cells = [cell.strip() for cell in header.strip("│ ").split("│")]
    assert cells[:4] == ["plugin", "item", "action", "detail"]


def test_remove_finds_a_project_by_its_name(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ-7")
    result = _cli("project", "remove", "PROJ-7", "--dry-run", "-f", "json")
    assert result.exit_code == 0, result.output
    assert _rows(result)[-1]["item"] == str(project.root)
    assert _cli("project", "remove", "nope", "--dry-run").exit_code == 2


def test_release_lets_go_of_one_owners_holds_without_a_prompt(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    preview = first.release(REPOS, items=[WEB], dry_run=True)
    assert [(r.action, r.detail) for r in preview] == [("planned", "deletes the repo")]  # type: ignore[attr-defined]
    rows = first.release(REPOS, items=[WEB])
    assert [r.action for r in rows] == ["released"]
    assert first.holds(REPOS) == (RepoKey(API),)


# --- prune --------------------------------------------------------------------------------


def test_prune_releases_missing_projects_and_skips_unreadable_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _two(tmp_path)
    away = make_project(tmp_path / "PROJ-300", holds={REPOS: [API]})
    shutil.rmtree(first.root)
    real = holds.view

    def view(ref: refs.Reference) -> Any:  # PROJ-300's drive is away
        seen = real(ref)
        if isinstance(seen, HolderView) and seen.holder.name == away.id:
            return seen.model_copy(update={"state": "unreadable"})
        return seen

    monkeypatch.setattr(holds, "view", view)
    import untaped.core.project_lifecycle as lifecycle

    real_state = lifecycle.anchor_state
    monkeypatch.setattr(
        lifecycle,
        "anchor_state",
        lambda ref: "unreadable" if ref.holder and ref.holder.name == away.id else real_state(ref),
    )
    dry = _cli("project", "prune", "--dry-run", "-f", "json")
    assert dry.exit_code == 0, dry.output
    assert {r["holder"] for r in _rows(dry)} == {"project PROJ-123"}
    assert f"skipped {away.root}: it can't be read now" in dry.stderr
    result = _cli("project", "prune", "--yes", "-f", "json")
    assert result.exit_code == 0, result.output
    assert [r["action"] for r in _rows(result)] == ["released", "released", "removed"]
    assert OWNER.deleted == [WEB]
    assert not refs.reference_file(holder_of(first)).exists()
    assert refs.reference_file(holder_of(second)).exists()
    assert refs.reference_file(holder_of(away)).exists()


def test_prune_refuses_a_present_project(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    result = _cli("project", "prune", str(project.root))
    assert result.exit_code == 1
    assert "prune removes only projects whose folder is gone" in result.stderr


# --- move ---------------------------------------------------------------------------------


def test_move_moves_the_folder_and_owners_repair(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    new = tmp_path / "archive" / "PROJ-123"
    result = _cli("project", "move", str(first.root), str(new), "-f", "json")
    assert result.exit_code == 0, result.output
    assert [(r["plugin"], r["action"], r["detail"]) for r in _rows(result)] == [
        ("untaped", "moved", "moved the folder; its reference follows"),
        ("acme", "repaired", "repaired worktree api"),
        ("acme", "repaired", "repaired worktree web"),
    ]
    assert not first.root.exists() and (new / ".untaped" / "project.json").exists()
    ref = refs.read_reference(refs.reference_file(holder_of(first)))
    assert (ref.anchor, ref.moved_from) == (new, ())
    assert {ask.moved_from for ask in OWNER.moves} == {first.root}


def test_a_failed_repair_is_retried_by_later_writing_uses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    new = tmp_path / "moved"
    OWNER.raise_moved = 2
    result = _cli("project", "move", str(first.root), str(new), "-f", "json")
    assert result.exit_code == 1
    assert [r["action"] for r in _rows(result)] == ["moved", "failed"]
    ref = refs.read_reference(refs.reference_file(holder_of(first)))
    assert (ref.anchor, ref.moved_from) == (new, (first.root,))
    moved = Project(id=first.id, root=new)
    capsys.readouterr()
    moved.hold(REPOS, API)  # a writing use retries, fails again, and only warns
    assert "could not repair github.com/acme/api" in capsys.readouterr().err
    assert refs.read_reference(refs.reference_file(holder_of(first))).moved_from == (first.root,)
    moved.hold(REPOS, API)  # and again: now it succeeds
    assert refs.read_reference(refs.reference_file(holder_of(first))).moved_from == ()
    assert len(OWNER.moves) == 3


def test_a_hand_move_on_one_filesystem_is_repaired_by_a_writing_use(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    new = tmp_path / "elsewhere"
    first.root.rename(new)
    from untaped.sdk.projects import _forget, current_project

    _forget()
    os.chdir(new)
    try:
        current_project().hold(REPOS, WEB)
    finally:
        os.chdir(tmp_path)
    ref = refs.read_reference(refs.reference_file(holder_of(first)))
    assert (ref.anchor, ref.moved_from) == (new, ())
    assert [ask.moved_from for ask in OWNER.moves] == [first.root]


def test_move_follows_a_folder_moved_by_hand_and_adopts_one_without_a_marker(
    tmp_path: Path,
) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    hand = tmp_path / "by-hand"
    first.root.rename(hand)
    result = _cli("project", "move", str(first.root), str(hand), "-f", "json")
    assert result.exit_code == 0, result.output
    assert _rows(result)[0]["action"] == "repaired"
    assert refs.read_reference(refs.reference_file(holder_of(first))).anchor == hand

    renamed = tmp_path / "renamed"
    hand.rename(renamed)
    (renamed / ".untaped" / "project.json").unlink()  # a folder that lost its marker
    needs_yes = _cli("project", "move", str(hand), str(renamed))
    assert needs_yes.exit_code == 2
    adopted = _cli("project", "move", str(hand), str(renamed), "--yes", "-f", "json")
    assert adopted.exit_code == 0, adopted.output
    marker = json.loads((renamed / ".untaped" / "project.json").read_text(encoding="utf-8"))
    assert marker["id"] == first.id
    assert refs.read_reference(refs.reference_file(holder_of(first))).anchor == renamed


def test_move_refuses_another_project_an_existing_folder_and_another_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_project(tmp_path / "PROJ")
    other = make_project(tmp_path / "OTHER")
    taken = _cli("project", "move", str(first.root), str(other.root))
    assert taken.exit_code == 1 and "is another project; nothing moved" in taken.stderr
    (tmp_path / "plain").mkdir()
    exists = _cli("project", "move", str(first.root), str(tmp_path / "plain"))
    assert exists.exit_code == 1 and "already exists; nothing moved" in exists.stderr
    import untaped.core.project_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_device", lambda path: hash(path.name))
    far = _cli("project", "move", str(first.root), str(tmp_path / "far"))
    assert far.exit_code == 1 and "different filesystems" in far.stderr
    assert first.root.exists()


def test_move_dry_run_changes_nothing(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    result = _cli(
        "project", "move", str(first.root), str(tmp_path / "new"), "--dry-run", "-f", "json"
    )
    assert result.exit_code == 0, result.output
    assert [r["action"] for r in _rows(result)] == ["planned", "planned"]
    assert first.root.exists() and OWNER.moves == []


def test_known_projects_lists_every_record_holding_or_not(tmp_path: Path) -> None:
    empty = make_project(tmp_path / "EMPTY")
    full = make_project(tmp_path / "FULL", holds={REPOS: [API]})
    (refs.REFS.place / "project-ffffffffffff.json").write_text("{", encoding="utf-8")
    seen = known_projects()
    assert {h.holder.name for h in seen if isinstance(h, HolderView)} == {empty.id, full.id}
    assert sum(isinstance(h, UnknownHolder) for h in seen) == 1


# --- plugin holder kinds (internal) ---------------------------------------------------------


def test_a_plugin_holder_kind_round_trips_and_keeps_its_holds_when_it_cannot_tell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(holds, "_KINDS", {})
    anchor = tmp_path / "job"
    anchor.mkdir()
    jobs = HolderKind(
        "acme-ci",
        "job",
        inspect=lambda holder, at: Sighting(state="missing", label=f"job {holder.name}"),
    )
    assert jobs.kind == "acme_ci_job"
    job = jobs.holder("build-42", anchor=anchor)
    job.hold(REPOS, API)
    written = refs.read_reference(refs.REFS.place / "acme_ci_job-build-42.json")
    assert (written.holder, written.anchor) == (
        RefHolder(kind="acme_ci_job", name="build-42"),
        anchor,
    )
    assert job.holds(REPOS) == (RepoKey(API),)
    [seen] = [h for h in REPOS.holders(API) if isinstance(h, HolderView)]
    assert (seen.label, seen.state) == ("job build-42", "missing")

    def broken(holder: RefHolder, at: Path) -> Sighting:
        raise OSError("can't look")

    monkeypatch.setitem(holds._KINDS, jobs.kind, HolderKind("acme-ci", "job", inspect=broken))
    [seen] = [h for h in REPOS.holders(API) if isinstance(h, HolderView)]
    assert seen.state == "unreadable"
    monkeypatch.setattr(holds, "_KINDS", {})  # its plugin isn't installed
    [seen] = [h for h in REPOS.holders(API) if isinstance(h, HolderView)]
    assert seen.state == "unreadable" and "isn't installed" in seen.label
    [plan] = plan_holds(seen, [REPOS.target(API)])
    assert plan.blocked and plan.failure is not None  # a holder that can't be read releases nothing
    project = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    [keep] = plan_release(project, REPOS)  # the job still counts as a holder
    assert isinstance(keep, LetGo) and keep.preview == "keeps the repo: another project holds it"


def test_the_removal_preview_of_a_copy_holds_nothing(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    copy = tmp_path / "COPY"
    shutil.copytree(first.root, copy)
    from untaped.sdk.projects import _forget, project_at

    _forget()
    found = project_at(copy)
    assert found is not None and found.copy_of == first.root
    removal = plan_removal(found, force=())
    assert isinstance(removal, Removal) and removal.plans == []


@pytest.mark.parametrize(
    ("args", "code", "message"),
    [
        (("PROJ", "PROJ"), 2, "OLD and NEW are the same folder"),
        (("PROJ/sub", "x"), 2, "is not a project's folder"),
        (("gone", "also-gone"), 2, "no project recorded at"),
        (("PROJ", "PROJ/inner"), 2, "is inside"),
        (("PROJ", "OUTER/inner"), 2, "is a project; projects don't nest"),
    ],
)
def test_move_refuses_what_it_cannot_do(
    tmp_path: Path, args: tuple[str, str], code: int, message: str
) -> None:
    make_project(tmp_path / "PROJ")
    (tmp_path / "PROJ" / "sub").mkdir()
    make_project(tmp_path / "OUTER")
    result = _cli("project", "move", *(str(tmp_path / arg) for arg in args))
    assert result.exit_code == code, result.output
    assert message in result.stderr


def test_move_with_both_folders_gone_says_so(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    shutil.rmtree(project.root)
    result = _cli("project", "move", str(project.root), str(tmp_path / "nowhere"))
    assert result.exit_code == 1
    assert "nothing moved" in result.stderr and "name where it is now as NEW" in result.stderr


def test_move_refuses_to_adopt_a_folder_inside_another_project(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    outer = make_project(tmp_path / "OUTER")
    shutil.rmtree(project.root)
    (outer.root / "inner").mkdir()
    result = _cli("project", "move", str(project.root), str(outer.root / "inner"), "--yes")
    assert result.exit_code == 2
    assert "projects don't nest" in result.stderr


def test_remove_finds_a_missing_project_by_name_and_refuses_ambiguity(tmp_path: Path) -> None:
    gone = make_project(tmp_path / "a" / "PROJ")
    shutil.rmtree(gone.root)
    result = _cli("project", "remove", "PROJ", "--yes", "-f", "json")
    assert result.exit_code == 0, result.output
    assert _rows(result)[-1]["detail"] == "removed the project's record"
    make_project(tmp_path / "b" / "PROJ")
    make_project(tmp_path / "c" / "PROJ")
    ambiguous = _cli("project", "remove", "PROJ", "--dry-run")
    assert ambiguous.exit_code == 2 and "2 projects are named 'PROJ'" in ambiguous.stderr
    outside = tmp_path / "plain"
    outside.mkdir()
    assert "is not a project's folder" in _cli("project", "remove", str(outside)).stderr
    assert "no project recorded at" in _cli("project", "remove", "./nope", "--dry-run").stderr


def test_a_preview_refuses_what_the_real_run_would(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    refs.reference_file(holder_of(project)).write_text("{", encoding="utf-8")
    result = _cli("project", "remove", str(project.root), "--dry-run")
    assert result.exit_code == 4
    assert "cannot read project PROJ's record" in result.stderr


def test_an_unreadable_project_is_not_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "PROJ")
    record = refs.read_reference(refs.reference_file(holder_of(project)))
    import untaped.sdk.projects as projects_module

    monkeypatch.setattr(projects_module, "anchor_state", lambda ref: "unreadable")
    with pytest.raises(ConfigError, match="can't be read now, so it keeps what it holds"):
        plan_removal(record, force=())


def test_remove_keeps_a_gitignore_someone_edited(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    gitignore = project.root / ".untaped" / ".gitignore"
    untouched = gitignore.read_text(encoding="utf-8")
    gitignore.write_text(untouched + "/notes/\n", encoding="utf-8")
    result = _cli("project", "remove", str(project.root), "--yes", "-f", "json")
    assert result.exit_code == 0, result.output
    assert gitignore.read_text(encoding="utf-8") == untouched + "/notes/\n"


def test_remove_keeps_lock_named_files_that_are_not_cores(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    folder = project.root / ".untaped"
    (folder / "mine.lock").mkdir()
    (folder / "mine.lock" / "precious.txt").write_text("x", encoding="utf-8")
    (folder / "recipe.lock").write_text("pinned\n", encoding="utf-8")
    (folder / "state.yml.lock").write_text("", encoding="utf-8")
    (folder / "plugins").write_text("not a folder", encoding="utf-8")
    result = _cli("project", "remove", str(project.root), "--yes", "-f", "json")
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in folder.iterdir()) == ["mine.lock", "plugins", "recipe.lock"]
    assert (folder / "mine.lock" / "precious.txt").exists()


def test_move_refuses_to_retarget_the_original_onto_a_copy(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    copy = tmp_path / "COPY"
    shutil.copytree(first.root, copy)
    result = _cli("project", "move", str(first.root), str(copy))
    assert result.exit_code == 1
    assert "is a copy of project PROJ" in result.stderr and "--new-id" in result.stderr
    assert refs.read_reference(refs.reference_file(holder_of(first))).anchor == first.root
    assert OWNER.moves == []


def test_the_same_move_again_retries_a_failed_repair(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    new = tmp_path / "moved"
    OWNER.raise_moved = 1
    assert _cli("project", "move", str(first.root), str(new)).exit_code == 1
    again = _cli("project", "move", str(first.root), str(new), "-f", "json")
    assert again.exit_code == 0, again.output
    assert [r["action"] for r in _rows(again)] == ["repaired", "repaired"]
    assert refs.read_reference(refs.reference_file(holder_of(first))).moved_from == ()


def test_a_broken_owner_keeps_a_moves_repair_pending(tmp_path: Path) -> None:
    broken = PluginSpec(
        name="acme", stores=(REPOS, ItemStore("acme", "repos", release=_release, moved=None))
    )
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    new = tmp_path / "moved"
    result = _cli("project", "move", str(first.root), str(new), "-f", "json", specs=(broken,))
    assert result.exit_code == 0, result.output
    assert _rows(result)[1]["action"] == "skipped"
    assert refs.read_reference(refs.reference_file(holder_of(first))).moved_from == (first.root,)
    gone = _cli("project", "move", str(first.root), str(new), "-f", "json", specs=())
    assert gone.exit_code == 0  # an owner that isn't installed has nothing to run
    assert refs.read_reference(refs.reference_file(holder_of(first))).moved_from == ()


def test_a_listed_missing_project_that_came_back_is_not_removed(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    record = refs.read_reference(refs.reference_file(holder_of(project)))
    with pytest.raises(UntapedError, match=r"is at .* again, so it isn't removed"):
        plan_removal(record, force=())
    assert (project.root / ".untaped" / "project.json").exists()


def test_a_bare_name_is_a_project_name_even_beside_a_folder_of_that_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = make_project(tmp_path / "PROJ-123")
    other = make_project(tmp_path / "elsewhere" / "PROJ-200")
    (here.root / "PROJ-200").mkdir()
    monkeypatch.chdir(here.root)
    result = _cli("project", "remove", "PROJ-200", "--dry-run", "-f", "json")
    assert result.exit_code == 0, result.output
    assert _rows(result)[-1]["item"] == str(other.root)
    inside = _cli("project", "remove", "./PROJ-200", "--dry-run")
    assert inside.exit_code == 2 and "inside project PROJ-123" in inside.stderr


def test_force_names_are_normalised_and_unknown_ones_refused(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    OWNER.refuse = {WEB}
    spelled = _cli(
        "project",
        "remove",
        str(first.root),
        "--force",
        "GitHub.com/acme/web.git",
        "--dry-run",
        "-f",
        "json",
    )
    assert spelled.exit_code == 0
    assert {r["item"]: r["action"] for r in _rows(spelled)}[WEB] == "planned"
    unknown = _cli("project", "remove", str(first.root), "--force", "nope", "--dry-run")
    assert unknown.exit_code == 2 and "--force names nope" in unknown.stderr


def test_the_preview_of_a_hand_moved_project_asks_about_it_where_it_is(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [WEB]})
    new = tmp_path / "moved"
    first.root.rename(new)
    from untaped.sdk.projects import _forget

    _forget()
    found = Project(id=first.id, root=new)
    removal = plan_removal(found, force=())
    assert (removal.seen.state, removal.seen.anchor) == ("present", new)


def test_move_refuses_a_copy_of_a_hand_moved_original(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    hand = tmp_path / "hand"
    first.root.rename(hand)
    shutil.copytree(hand, tmp_path / "COPY")
    result = _cli("project", "move", str(hand), str(tmp_path / "COPY"))
    assert result.exit_code == 1 and "is a copy of project" in result.stderr
    assert refs.read_reference(refs.reference_file(holder_of(first))).anchor == first.root
    assert OWNER.moves == []


def test_move_from_a_copy_onto_the_recorded_folder_moves_nothing(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    copy = tmp_path / "COPY"
    shutil.copytree(first.root, copy)
    result = _cli("project", "move", str(copy), str(first.root))
    assert result.exit_code == 1
    assert f"{copy} is a copy of project PROJ at {first.root}" in " ".join(result.stderr.split())
    assert OWNER.moves == []


def test_a_pending_repair_is_retried_only_onto_the_folder_the_record_names(
    tmp_path: Path,
) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    moved, other = tmp_path / "moved", tmp_path / "other"
    OWNER.raise_moved = 1
    assert _cli("project", "move", str(first.root), str(moved)).exit_code == 1
    other.mkdir()
    result = _cli("project", "move", str(first.root), str(other), "--yes")
    assert result.exit_code == 1
    assert f"moved from {first.root} to {moved}" in result.stderr
    assert f"untaped project move {first.root} {moved}" in result.stderr
    record = refs.read_reference(refs.reference_file(holder_of(first)))
    assert (record.anchor, record.moved_from) == (moved, (first.root,))
    assert not (other / ".untaped").exists()


def test_a_folder_recorded_at_old_wins_over_another_projects_pending_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    OWNER.raise_moved = 1
    assert _cli("project", "move", str(first.root), str(tmp_path / "moved")).exit_code == 1
    second = make_project(tmp_path / "PROJ")  # a new project where the first one was
    hand = tmp_path / "hand"
    second.root.rename(hand)
    import untaped.core.project_lifecycle as lifecycle

    pending_first = sorted(lifecycle.project_references(), key=lambda r: not r.moved_from)
    monkeypatch.setattr(lifecycle, "project_references", lambda: pending_first)
    result = _cli("project", "move", str(second.root), str(hand), "-f", "json")
    assert result.exit_code == 0, result.output
    assert refs.read_reference(refs.reference_file(holder_of(second))).anchor == hand


def test_a_hold_taken_during_a_repair_stays_when_the_move_completes(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    OWNER.during_moved = [lambda: holds.add_hold(holder_of(first), REPOS, WEB)]
    result = _cli("project", "move", str(first.root), str(tmp_path / "moved"))
    assert result.exit_code == 0, result.output
    record = refs.read_reference(refs.reference_file(holder_of(first)))
    assert record.moved_from == ()
    assert set(record.targets) == {REPOS.target(API), REPOS.target(WEB)}


def test_move_refuses_a_symlinked_old(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ")
    link = tmp_path / "link"
    link.symlink_to(first.root)
    result = _cli("project", "move", str(link), str(tmp_path / "new"))
    assert result.exit_code == 2 and "is a symlink" in result.stderr
    assert first.root.exists()


def test_the_removal_preview_says_whether_the_folder_goes(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    empty = _cli("project", "remove", str(project.root), "--dry-run", "-f", "json")
    assert _rows(empty)[-1]["detail"].endswith("then the folder, which holds nothing else")
    (project.root / "notes.md").write_text("mine\n", encoding="utf-8")
    kept = _cli("project", "remove", str(project.root), "--dry-run", "-f", "json")
    assert _rows(kept)[-1]["detail"].endswith("keeps the folder (it holds your files)")


def test_a_plan_that_raises_exits_1_in_a_preview_too(tmp_path: Path) -> None:
    first, _ = _two(tmp_path)
    OWNER.raise_plan = {API}
    assert _cli("project", "remove", str(first.root), "--check").exit_code == 1
    assert _cli("project", "remove", str(first.root), "--dry-run").exit_code == 1


def test_prune_with_every_listed_project_skipped_has_nothing_to_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    record = refs.read_reference(refs.reference_file(holder_of(project)))
    import untaped.core.project_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "missing_projects", lambda: ([record], []))  # back since
    result = _cli("project", "prune", "--force", API)
    assert result.exit_code == 0, result.output
    assert "isn't removed" in result.stderr and "nothing to prune" in result.stderr
    assert refs.reference_file(holder_of(project)).exists()


def test_a_record_removed_since_it_was_listed_says_so(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ")
    record = refs.read_reference(refs.reference_file(holder_of(project)))
    record.file.unlink()
    with pytest.raises(UntapedError, match="was already removed"):
        plan_removal(record, force=())


def test_a_bare_word_that_is_neither_the_project_nor_a_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "PROJ")
    (project.root / "src").mkdir()
    monkeypatch.chdir(project.root)
    result = _cli("project", "remove", "src", "--dry-run")
    assert result.exit_code == 2 and "inside project PROJ" in result.stderr


def test_a_gone_project_is_found_through_a_symlinked_path(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    project = make_project(tmp_path / "real" / "PROJ")
    shutil.rmtree(project.root)
    result = _cli("project", "prune", str(tmp_path / "link" / "PROJ"), "--dry-run", "-f", "json")
    assert result.exit_code == 0, result.output
    assert _rows(result)[-1]["holder"] == "project PROJ"
    hand = tmp_path / "hand"
    result = _cli("project", "move", str(tmp_path / "link" / "PROJ"), str(hand))
    assert result.exit_code == 1 and "neither" in result.stderr  # found, then both gone


def test_forgetting_a_hold_takes_its_item_stores_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: ["pack:terraform"]})
    taken: list[tuple[str, str]] = []
    real = holds._target_lock

    def spy(plugin: str, store: str) -> Any:
        taken.append((plugin, store))
        return real(plugin, store)

    monkeypatch.setattr(holds, "_target_lock", spy)
    forced = _cli(
        "project", "remove", str(first.root), "--force", "pack:terraform", "--yes", specs=()
    )
    assert forced.exit_code == 0, forced.output
    assert ("acme", "repos") in taken


def test_move_refuses_one_copy_onto_another(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    for name in ("C1", "C2"):
        shutil.copytree(first.root, tmp_path / name)
    result = _cli("project", "move", str(tmp_path / "C1"), str(tmp_path / "C2"), "--yes")
    assert result.exit_code == 1
    assert f"are copies of project PROJ at {first.root}" in " ".join(result.stderr.split())
    assert refs.read_reference(refs.reference_file(holder_of(first))).anchor == first.root
    assert OWNER.moves == []


def test_the_copy_refusal_names_the_folder_the_record_knows_by_identity(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ")
    new = tmp_path / "NEW"
    first.root.rename(new)
    shutil.copytree(new, first.root)  # a copy where the original was
    result = _cli("project", "move", str(first.root), str(new))
    assert result.exit_code == 1
    assert f"{first.root} is a copy of project PROJ at {new}" in " ".join(result.stderr.split())


def test_a_pending_repair_follows_its_folder_moved_by_hand_again(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    moved, again = tmp_path / "moved", tmp_path / "again"
    OWNER.raise_moved = 1
    assert _cli("project", "move", str(first.root), str(moved)).exit_code == 1
    moved.rename(again)
    stale = _cli("project", "move", str(first.root), str(tmp_path / "nowhere"))
    assert stale.exit_code == 1  # moved is gone too: the hint names a move from it
    assert f"untaped project move {moved} NEW" in " ".join(stale.stderr.split())
    OWNER.moves.clear()
    result = _cli("project", "move", str(first.root), str(again), "-f", "json")
    assert result.exit_code == 0, result.output
    assert [ask.moved_from for ask in OWNER.moves] == [first.root]
    record = refs.read_reference(refs.reference_file(holder_of(first)))
    assert (record.anchor, record.moved_from) == (again, ())


def test_a_later_move_repairs_from_the_first_folder_still_unrepaired(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    moved, again = tmp_path / "moved", tmp_path / "again"
    OWNER.raise_moved = 1
    assert _cli("project", "move", str(first.root), str(moved)).exit_code == 1
    moved.rename(again)  # by hand, before the repair was retried
    OWNER.moves.clear()
    result = _cli("project", "move", str(moved), str(again), "-f", "json")
    assert result.exit_code == 0, result.output
    assert [ask.moved_from for ask in OWNER.moves] == [first.root, moved]  # pending one first
    assert refs.read_reference(refs.reference_file(holder_of(first))).moved_from == ()


def _sources() -> list[tuple[str, Path]]:
    return [(ask.item.root, ask.moved_from) for ask in OWNER.moves]


def test_a_move_back_still_repairs_from_the_folder_it_left(tmp_path: Path) -> None:
    first = make_project(tmp_path / "A", holds={REPOS: [API, WEB]})
    a, b = first.root, tmp_path / "B"
    OWNER.fail_moved = {WEB}
    assert _cli("project", "move", str(a), str(b)).exit_code == 1  # api repaired, web pending
    OWNER.fail_moved.clear()
    OWNER.moves.clear()
    result = _cli("project", "move", str(b), str(a), "-f", "json")
    assert result.exit_code == 0, result.output
    assert (API, b) in _sources() and (WEB, b) in _sources()
    record = refs.read_reference(refs.reference_file(holder_of(first)))
    assert (record.anchor, record.moved_from) == (a, ())


def test_a_hand_move_back_repairs_from_the_folder_it_left(tmp_path: Path) -> None:
    first = make_project(tmp_path / "A", holds={REPOS: [API, WEB]})
    a, b = first.root, tmp_path / "B"
    OWNER.fail_moved = {WEB}
    assert _cli("project", "move", str(a), str(b)).exit_code == 1
    OWNER.fail_moved.clear()
    OWNER.moves.clear()
    b.rename(a)
    from untaped.sdk.projects import _forget, project_at, writing

    _forget()
    here = project_at(a)
    assert here is not None
    with here.locked():
        writing(here)
    assert sorted(_sources()) == [(API, b), (WEB, b)]
    assert refs.read_reference(refs.reference_file(holder_of(first))).moved_from == ()


def test_a_chain_of_moves_asks_from_each_folder_until_every_repair_succeeds(
    tmp_path: Path,
) -> None:
    first = make_project(tmp_path / "A", holds={REPOS: [API, WEB]})
    a, b, c = first.root, tmp_path / "B", tmp_path / "C"
    OWNER.fail_moved = {WEB}
    assert _cli("project", "move", str(a), str(b)).exit_code == 1
    b.rename(c)  # by hand, while web's repair is still pending
    OWNER.moves.clear()
    assert _cli("project", "move", str(b), str(c)).exit_code == 1  # web fails again
    assert (API, a) in _sources() and (API, b) in _sources()
    assert refs.read_reference(refs.reference_file(holder_of(first))).moved_from == (a, b)
    OWNER.fail_moved.clear()
    OWNER.moves.clear()
    again = _cli("project", "move", str(b), str(c), "-f", "json")  # the same move, again
    assert again.exit_code == 0, again.output
    assert sorted(_sources()) == [(API, a), (API, b), (WEB, a), (WEB, b)]
    assert [(r["item"], r["action"]) for r in _rows(again)[1:]] == [
        (API, "repaired"),
        (WEB, "repaired"),
    ]  # one row per hold, whichever folders it was asked from
    record = refs.read_reference(refs.reference_file(holder_of(first)))
    assert (record.anchor, record.moved_from) == (c, ())


def test_two_copies_the_record_cannot_tell_apart_follow_the_move(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ", holds={REPOS: [API]})
    for name in ("C1", "C2"):
        shutil.copytree(first.root, tmp_path / name)
    shutil.rmtree(first.root)  # the original is gone: no folder has its identity
    result = _cli("project", "move", str(tmp_path / "C1"), str(tmp_path / "C2"), "-f", "json")
    assert result.exit_code == 0, result.output
    assert refs.read_reference(refs.reference_file(holder_of(first))).anchor == tmp_path / "C2"


def test_move_onto_a_symlink_to_old_is_the_same_folder(tmp_path: Path) -> None:
    first = make_project(tmp_path / "PROJ")
    (tmp_path / "link").symlink_to(first.root)
    result = _cli("project", "move", str(first.root), str(tmp_path / "link"))
    assert result.exit_code == 2 and "the same folder" in result.stderr


def test_a_failure_from_any_folder_is_the_row_shown_and_stays_pending(tmp_path: Path) -> None:
    first = make_project(tmp_path / "A", holds={REPOS: [API]})
    a, b, c = first.root, tmp_path / "B", tmp_path / "C"
    OWNER.raise_moved = 1
    assert _cli("project", "move", str(a), str(b)).exit_code == 1  # pending from A
    b.rename(c)
    asks: list[Path] = []

    def fail_from_b() -> None:  # the repair from A now works, the one from B fails
        asks.append(OWNER.moves[-1].moved_from)
        OWNER.raise_moved = int(asks[-1] == b)

    OWNER.during_moved = [fail_from_b]
    result = _cli("project", "move", str(b), str(c), "-f", "json")
    assert result.exit_code == 1
    assert asks == [a, b]
    assert [r["action"] for r in _rows(result)[1:]] == ["failed"]
    assert refs.read_reference(refs.reference_file(holder_of(first))).moved_from == (b,)


def test_a_folder_at_its_path_under_a_new_identity_lets_the_move_decide(tmp_path: Path) -> None:
    first = make_project(tmp_path / "P", holds={REPOS: [API]})
    away = tmp_path / "away"
    first.root.rename(away)
    shutil.copytree(away, first.root)  # restored at its path: a new identity
    (away / ".untaped" / "project.json").unlink()  # no folder keeps the recorded one
    shutil.copytree(first.root, tmp_path / "R")
    result = _cli("project", "move", str(first.root), str(tmp_path / "R"), "-f", "json")
    assert result.exit_code == 0, result.output
    assert refs.read_reference(refs.reference_file(holder_of(first))).anchor == tmp_path / "R"


def test_a_failure_from_an_earlier_folder_wins_over_a_later_repair(tmp_path: Path) -> None:
    first = make_project(tmp_path / "A", holds={REPOS: [API]})
    a, b, c = first.root, tmp_path / "B", tmp_path / "C"
    OWNER.raise_moved = 1
    assert _cli("project", "move", str(a), str(b)).exit_code == 1  # pending from A
    b.rename(c)
    asks: list[Path] = []

    def fail_from_a() -> None:  # the repair from A fails again, the one from B works
        asks.append(OWNER.moves[-1].moved_from)
        OWNER.raise_moved = int(asks[-1] == a)

    OWNER.during_moved = [fail_from_a]
    result = _cli("project", "move", str(b), str(c), "-f", "json")
    assert result.exit_code == 1
    assert asks == [a, b]
    [row] = _rows(result)[1:]
    assert row["action"] == "failed" and row["error"]["message"].startswith("raised RuntimeError")
    assert refs.read_reference(refs.reference_file(holder_of(first))).moved_from == (a,)


def test_one_item_held_in_two_stores_gets_a_row_from_each(tmp_path: Path) -> None:
    def mirrored(ask: MoveAsk[RepoKey]) -> str:
        raise RuntimeError("mirror repair failed")

    mirrors = ItemStore("acme", "mirrors", item=RepoKey, release=_release, moved=mirrored)
    spec = PluginSpec(name="acme", stores=(REPOS, mirrors))
    with bootstrap.kept_composition(quiet=True):
        bootstrap.compose_root(candidates=[plugin_candidate(spec)])
        first = make_project(tmp_path / "A", holds={REPOS: [API], mirrors: [API]})
    OWNER.raise_moved = 1
    result = _cli(
        "project", "move", str(first.root), str(tmp_path / "B"), "-f", "json", specs=(spec,)
    )
    assert result.exit_code == 1
    details = sorted(r["detail"] for r in _rows(result)[1:])
    assert details == [
        "raised RuntimeError: mirror repair failed",
        "raised RuntimeError: worktree repair failed",
    ]
