"""Dotfiles command tree: subscribe, repos, items, enable, status, diff, apply, sync, remove."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from untaped.sdk import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    OutputFormat,
    UntapedError,
    UsageError,
    YesOption,
    create_app,
    echo,
    emit,
    finish,
    plural,
    q,
    report_error,
    report_errors,
    report_row_errors,
    ui_context,
    unified_diff_text,
    writes,
)
from untaped_dotfiles.application import (
    DisableItems,
    EnableItems,
    Placement,
    Step,
    SubscribeRepo,
    UnsubscribeRepo,
    pick_enabled,
)
from untaped_dotfiles.cli.common import (
    AllOption,
    ItemsArg,
    RepoOption,
    Services,
    services,
    utc_now,
)
from untaped_dotfiles.domain.models import Policy, item_id
from untaped_dotfiles.domain.records import (
    ItemOutcome,
    PlaceOutcome,
    RepoOutcome,
)
from untaped_dotfiles.domain.status import needs_attention

app = create_app(
    name="dotfiles",
    help=(
        "Place dotfiles from subscribed repos, with a policy per item per machine. "
        "Experimental: may change in a minor release."
    ),
)

ITEM = "dotfiles.item"
ITEM_OUTCOME = "dotfiles.item_outcome"
REPO = "dotfiles.repo"
REPO_OUTCOME = "dotfiles.repo_outcome"
STATUS = "dotfiles.status"
STATUS_SUMMARY = "dotfiles.status.summary"
APPLY_OUTCOME = "dotfiles.apply_outcome"
SYNC_OUTCOME = "dotfiles.sync_outcome"
REMOVE_OUTCOME = "dotfiles.remove_outcome"

PolicyOption = Annotated[
    Policy | None,
    Parameter(
        name="--policy",
        help="sync (new versions apply as they arrive), once (apply, then leave alone) or "
        "manual (report; apply when asked). Default: the manifest's suggestion.",
    ),
]


# -- repos ---------------------------------------------------------------------


@writes
def subscribe_command(
    source: Annotated[
        str, Parameter(name="SOURCE", help="A git URL to clone, or the path of a checkout.")
    ],
    /,
    *,
    name: Annotated[
        str | None,
        Parameter(name="--name", help="Repo name. Default: the last path segment of SOURCE."),
    ] = None,
    manifest: Annotated[
        str | None,
        Parameter(name="--manifest", help="Manifest path inside the repo. Default: dotfiles.yml."),
    ] = None,
    ref: Annotated[
        str | None,
        Parameter(name="--ref", help="Branch to follow. Default: the repo's default branch."),
    ] = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Subscribe to a repo: clone a URL under dotfiles.repos_dir, or register a checkout.

    Nothing is enabled yet: the manifest's items are listed, and `enable`
    picks them. A registered checkout is fetched but never pulled.
    """
    with report_errors():
        svc = services()
        subscribe = SubscribeRepo(
            svc.store, svc.git, svc.inventory, repos_dir=svc.repos_dir, now=utc_now
        )
        record = subscribe(source, name=name, manifest=manifest, ref=ref)
        rows = svc.catalog.rows(record.name)
        emit(rows, fmt=fmt, columns=columns, kind=ITEM, empty="No items in the manifest.")
        ui_context(strict=False).success(
            f"subscribed {q(record.name)} ({plural(len(rows), 'item')}); "
            f"run `untaped dotfiles enable ITEM` to pick items"
        )


@writes(destructive=True)
def unsubscribe_command(
    name: Annotated[str, Parameter(name="NAME", help="The subscribed repo's name.")],
    /,
    *,
    delete_clone: Annotated[
        bool,
        Parameter(
            name="--delete-clone",
            negative="",
            help="Also delete the clone the tool made (never a registered checkout).",
        ),
    ] = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Forget a repo. Refuses while any of its items is enabled."""
    with report_errors():
        svc = services()
        unsubscribe = UnsubscribeRepo(svc.store)
        record = unsubscribe.plan(name)
        what = f"forget repo {q(name)}" + (" and delete its clone" if delete_clone else "")
        if dry_run:
            emit(
                [RepoOutcome(name=name, action="planned", detail=what)],
                fmt=fmt,
                columns=columns,
                kind=REPO_OUTCOME,
            )
            return
        ui = ui_context(strict=False)
        ui.confirm_or_cancel(
            f"{what.capitalize()}?",
            assume_yes=yes,
            refusal="unsubscribe requires --yes when not interactive",
        )
        row = unsubscribe(record, delete_clone=delete_clone)
        emit([row], fmt=fmt, columns=columns, kind=REPO_OUTCOME)
        ui.success(f"unsubscribed {q(name)}")


def repos_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List subscribed repos with their branch and how far behind origin they are."""
    with report_errors():
        svc = services()
        rows = svc.reader.repo_rows()
        emit(rows, fmt=fmt, columns=columns, kind=REPO, empty="No repos subscribed.")


# -- items ---------------------------------------------------------------------


def items_command(
    *,
    repo: RepoOption = None,
    all_items: Annotated[
        bool,
        Parameter(
            name="--all", negative="", help="Include items that do not apply on this machine."
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List manifest items with the suggested policy and this machine's choice."""
    with report_errors():
        svc = services()
        rows = svc.catalog.rows(repo, include_excluded=all_items)
        emit(rows, fmt=fmt, columns=columns, kind=ITEM, empty="No items found.")


@writes
def enable_command(
    items: Annotated[list[str] | None, Parameter(name="ITEM", help="Item names to enable.")] = None,
    /,
    *,
    repo: RepoOption = None,
    policy: PolicyOption = None,
    skip: Annotated[
        list[str] | None,
        Parameter(
            name="--skip",
            negative="",
            consume_multiple=False,
            help="File of the item to leave out on this machine, by its name (repeatable).",
        ),
    ] = None,
    every: AllOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Enable items on this machine with a policy. Nothing is placed until `apply` or `sync`.

    Enabling again changes the policy or the skipped files and keeps the rest.
    """
    with report_errors():
        if bool(items) == every:
            raise UsageError("pass ITEM names or --all, not both or neither")
        svc = services()
        enable = EnableItems(svc.store, svc.catalog, now=utc_now)
        targets = enable.targets(items or [], repo=repo, every=every)
        before = {c.id for c in svc.store.items()}
        rows = enable(targets, policy=policy, skip=skip or [])
        outcomes = [
            ItemOutcome(
                repo=row.repo,
                name=row.name,
                action="updated" if item_id(row.repo, row.name) in before else "created",
                policy=row.policy,
                detail=f"skips {', '.join(row.skip)}" if row.skip else "",
            )
            for row in rows
        ]
        emit(outcomes, fmt=fmt, columns=columns, kind=ITEM_OUTCOME)
        ui_context(strict=False).success(
            f"enabled {plural(len(rows), 'item')}; run `untaped dotfiles apply` to place them"
        )


@writes
def disable_command(
    items: Annotated[
        list[str] | None, Parameter(name="ITEM", help="Item names to disable.")
    ] = None,
    /,
    *,
    repo: RepoOption = None,
    every: AllOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Stop managing items on this machine. Placed files stay; `remove` deletes them."""
    with report_errors():
        if bool(items) == every:
            raise UsageError("pass ITEM names or --all, not both or neither")
        svc = services()
        choices = DisableItems(svc.store)(items or [], repo=repo, every=every)
        rows = [
            ItemOutcome(repo=c.repo, name=c.name, action="deleted", policy=c.policy)
            for c in choices
        ]
        emit(rows, fmt=fmt, columns=columns, kind=ITEM_OUTCOME, empty="No items to disable.")


# -- status ----------------------------------------------------------------------


def status_command(
    items: ItemsArg = None,
    /,
    *,
    repo: RepoOption = None,
    check: Annotated[
        bool,
        Parameter(
            name="--check",
            negative="",
            help="Exit 3 when any path needs the user (behind, modified, conflict, missing, "
            "orphan, or one that could not be read).",
        ),
    ] = False,
    all_paths: Annotated[
        bool,
        Parameter(name="--all", negative="", help="Include paths excluded on this machine."),
    ] = False,
    summary_only: Annotated[
        bool,
        Parameter(
            name="--summary",
            negative="",
            help="Print the counts (what status.json holds) instead of one row per path.",
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show the state of every placed path, offline. Also writes status.json and attention."""
    with report_errors():
        svc = services()
        choices = pick_enabled(svc.store, items, repo=repo)
        resolved = svc.inventory.placements(choices, include_excluded=all_paths)
        _report_problems(resolved.problems)
        rows = svc.reader.rows(resolved.placements)
        summary = svc.reader.summary(rows, repos_behind=svc.reader.repos_behind())
        if not items and repo is None:
            svc.store.write_status(summary)
        if summary_only:
            emit(summary, fmt=fmt, columns=columns, kind=STATUS_SUMMARY)
        else:
            emit(rows, fmt=fmt, columns=columns, kind=STATUS, empty="No enabled items.")
        report_row_errors(rows, item=lambda row: f"{row.item}/{row.source}")
        failed = bool(resolved.problems) or any(row.error is not None for row in rows)
    finish(failed, predicate_hit=check and summary.attention > 0)


def diff_command(
    items: ItemsArg = None,
    /,
    *,
    repo: RepoOption = None,
) -> None:
    """Show what `apply` would change in copy and merge files (a unified diff on stdout)."""
    with report_errors():
        svc = services()
        choices = pick_enabled(svc.store, items, repo=repo)
        resolved = svc.inventory.placements(choices)
        _report_problems(resolved.problems)
        for text in _diffs(svc, resolved.placements):
            echo(text, nl=False)
    finish(bool(resolved.problems))


def _diffs(svc: Services, placements: Sequence[Placement]) -> list[str]:
    svc.evaluator.prime_changes(placements)
    out: list[str] = []
    for p in placements:
        if p.mode == "link" or p.excluded:
            continue
        found = svc.evaluator.evaluate(p)
        if found.state not in ("pending", "foreign", "behind", "modified", "conflict"):
            continue
        before = svc.placer.render(p.target)
        data = svc.evaluator.tree(p.repo).read(p.source)
        if p.mode == "merge" and p.fmt is not None:
            after = svc.placer.preview_merge(data, p.target, fmt=p.fmt)
        else:
            after = data.decode("utf-8", errors="replace")
        out.append(unified_diff_text(before, after, path=_shown(svc, p.target)))
    return out


def _shown(svc: Services, target: Path) -> str:
    try:
        return "~/" + target.relative_to(svc.home).as_posix()
    except ValueError:
        return str(target)


# -- apply, sync, remove -----------------------------------------------------------


@writes(destructive=True)
def apply_command(
    items: ItemsArg = None,
    /,
    *,
    repo: RepoOption = None,
    force: Annotated[
        bool,
        Parameter(
            name="--force",
            negative="",
            help="Replace paths edited on this machine too (the local version is kept aside).",
        ),
    ] = False,
    show_diff: Annotated[
        bool,
        Parameter(
            name="--diff",
            negative="",
            help="Show a unified diff of copy and merge changes before confirming.",
        ),
    ] = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Place the named enabled items (all of them by default), whatever their policy.

    Fetches their repos first and fast-forwards a clone when the item has a
    link file in it, which moves every other link file on that clone too; the
    plan lists those paths. Refuses paths edited on this machine unless --force.
    Rewrites status.json and attention afterwards.
    """
    with report_errors():
        svc = services()
        with svc.store.locked():
            choices = pick_enabled(svc.store, items, repo=repo)
            resolved = svc.inventory.placements(choices)
            _report_problems(resolved.problems)
            placements = resolved.placements
            pulled = _pull_repos(svc, placements)
            moved = _moving_with(svc, pulled, placements) if pulled else []
            if any(row.error is not None for row in pulled.values()):
                report_row_errors(list(pulled.values()), item=lambda row: row.name)
            steps = svc.applier.plan_apply(placements, force=force)
            planned = svc.applier.planned(steps) + moved
            if dry_run:
                emit(planned, fmt=fmt, columns=columns, kind=APPLY_OUTCOME)
                return
            todo = [s for s in steps if s.action in ("apply", "remove")]
            if todo or moved:
                _confirm(
                    svc,
                    planned,
                    fmt=fmt,
                    verb="apply",
                    message=f"Apply {plural(len(todo), 'path')}?",
                    yes=yes,
                    diff=(show_diff and _diffs(svc, [s.placement for s in todo])) or [],
                )
                _fast_forward(svc, pulled, placements)
                steps = svc.applier.replan(steps, force=force)
            rows = svc.applier.execute(steps) + moved
            _refresh_status(svc)
        emit(rows, fmt=fmt, columns=columns, kind=APPLY_OUTCOME, empty="Nothing to apply.")
        report_row_errors(rows, item=lambda row: f"{row.item}/{row.source}")
        failed = bool(resolved.problems) or any(
            row.action in ("failed", "conflict") for row in rows
        )
        failed = failed or any(row.error is not None for row in pulled.values())
    finish(failed)


@writes
def sync_command(
    *,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Fetch every repo, apply the `sync` items, report the rest. Safe for a timer.

    Never prompts and never overwrites a path edited on this machine. Exits 3
    when anything needs the user, 1 when an apply or a git operation failed.
    """
    with report_errors():
        svc = services()
        with svc.store.locked():
            choices = svc.store.items()
            repos = svc.store.repos()
            repo_rows = {r.name: r for r in svc.applier.fetch_all(repos)}
            resolved = svc.inventory.placements(choices)  # manifests as just fetched
            _report_problems(resolved.problems)
            placements = resolved.placements
            if not dry_run:
                for repo in repos:
                    if repo.name in repo_rows:
                        continue
                    held = svc.applier.held_back_by(repo, placements)
                    repo_rows[repo.name] = svc.applier.fast_forward(repo, held=held)
            svc.evaluator.reset()
            steps = svc.applier.plan_sync(placements)
            rows = svc.applier.planned(steps) if dry_run else svc.applier.execute(steps)
            if dry_run:
                attention = _planned_attention(steps)
            else:
                # removed orphans no longer have a placement, so resolve again for the status
                status_rows = svc.reader.rows(svc.inventory.placements(choices).placements)
                summary = svc.reader.summary(status_rows, repos_behind=svc.reader.repos_behind())
                svc.store.write_status(summary)
                attention = summary.attention
        emit(rows, fmt=fmt, columns=columns, kind=SYNC_OUTCOME, empty="Nothing enabled.")
        _note_repos(list(repo_rows.values()))
        report_row_errors(list(repo_rows.values()), item=lambda row: row.name)
        report_row_errors(rows, item=lambda row: f"{row.item}/{row.source}")
        failed = (
            bool(resolved.problems)
            or any(row.action == "failed" for row in rows)
            or any(row.error is not None for row in repo_rows.values())
        )
    finish(failed, predicate_hit=attention > 0)


@writes(destructive=True)
def remove_command(
    items: Annotated[
        list[str] | None, Parameter(name="ITEM", help="Enabled items whose paths to remove.")
    ] = None,
    /,
    *,
    repo: RepoOption = None,
    every: AllOption = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Delete the paths the tool placed for items and disable them.

    Links and unedited copies are deleted, merged keys are taken back out,
    and a copy edited on this machine is kept aside instead of deleted.
    """
    with report_errors():
        if bool(items) == every:
            raise UsageError("pass ITEM names or --all, not both or neither")
        svc = services()
        with svc.store.locked():
            choices = pick_enabled(svc.store, items, repo=repo)
            resolved = svc.inventory.placements(choices)
            _report_problems(resolved.problems)
            steps = svc.applier.plan_apply(resolved.placements, force=True)
            planned = svc.applier.preview_removal(steps)
            if dry_run:
                emit(planned, fmt=fmt, columns=columns, kind=REMOVE_OUTCOME)
                return
            recorded = [s for s in steps if s.found.record is not None]
            _confirm(
                svc,
                planned,
                fmt=fmt,
                verb="remove",
                message=f"Remove {plural(len(recorded), 'path')} and disable "
                f"{plural(len(choices), 'item')}?",
                yes=yes,
            )
            rows = svc.applier.remove_all(steps)
            for choice in choices:
                svc.store.remove_item(choice.repo, choice.name)
            _refresh_status(svc)
        emit(rows, fmt=fmt, columns=columns, kind=REMOVE_OUTCOME, empty="Nothing to remove.")
        report_row_errors(rows, item=lambda row: f"{row.item}/{row.source}")
        failed = bool(resolved.problems) or any(row.action == "failed" for row in rows)
    finish(failed)


# -- helpers ---------------------------------------------------------------------


def _refresh_status(svc: Services) -> None:
    """Rewrite status.json and attention after a change, so the prompt segment is current."""
    svc.evaluator.reset()
    rows = svc.reader.rows(svc.inventory.placements(svc.store.items()).placements)
    svc.store.write_status(svc.reader.summary(rows, repos_behind=svc.reader.repos_behind()))


def _report_problems(problems: Sequence[tuple[str, UntapedError]]) -> None:
    for item, exc in problems:
        report_error(exc, item=item)


def _pull_repos(svc: Services, placements: Sequence[Placement]) -> dict[str, RepoOutcome]:
    """Fetch the managed repos the selected link files read from; the clone moves later."""
    repos = {p.repo.name: p.repo for p in placements if p.mode == "link" and p.repo.managed}
    rows = {row.name: row for row in svc.applier.fetch_all(list(repos.values()))}
    for name in repos:
        rows.setdefault(name, RepoOutcome(name=name, action="planned", detail="fast-forward"))
    svc.evaluator.reset()
    return rows


def _fast_forward(
    svc: Services, pulled: dict[str, RepoOutcome], placements: Sequence[Placement]
) -> None:
    """Fast-forward every clone ``_pull_repos`` planned; ``apply`` never holds one back."""
    for name, row in list(pulled.items()):
        if row.action != "planned":
            continue
        repo = next(p.repo for p in placements if p.repo.name == name)
        pulled[name] = svc.applier.fast_forward(repo)
    svc.evaluator.reset()


def _moving_with(
    svc: Services, pulled: dict[str, RepoOutcome], selected: Sequence[Placement]
) -> list[PlaceOutcome]:
    """Link paths of other items that move when the selected items' clones are pulled."""
    chosen = {p.key for p in selected}
    every = svc.inventory.placements(svc.store.items()).placements
    others = [
        p
        for p in every
        if p.mode == "link" and p.repo.name in pulled and p.key not in chosen and not p.excluded
    ]
    if not others:
        return []
    svc.evaluator.prime_changes(others)
    rows: list[PlaceOutcome] = []
    for p in others:
        found = svc.evaluator.evaluate(p)
        if found.state in ("behind", "conflict"):
            rows.append(
                PlaceOutcome(
                    repo=p.repo.name,
                    item=p.item,
                    source=p.source,
                    mode=p.mode,
                    action="updated",
                    state=found.state,
                    detail=f"moves with the clone of {p.repo.name} ({p.choice.policy} item)",
                    target_path=p.target,
                )
            )
    return rows


def _confirm(
    svc: Services,
    planned: Sequence[PlaceOutcome],
    *,
    fmt: OutputFormat,
    verb: str,
    message: str,
    yes: bool,
    diff: Sequence[str] = (),
) -> None:
    ui = ui_context(strict=False)

    def preview() -> None:
        for text in diff:
            echo(text, err=True, nl=False)
        rows = [row.model_dump(mode="json") for row in planned]
        echo(ui.collection(rows, fmt=fmt), err=True)

    ui.confirm_or_cancel(
        message,
        assume_yes=yes,
        refusal=f"{verb} requires --yes when not interactive",
        preview=preview,
    )


def _note_repos(rows: Sequence[RepoOutcome]) -> None:
    ui = ui_context(strict=False)
    for row in rows:
        if row.action == "skipped":
            ui.message("info", f"{row.name}: {row.detail}")


def _planned_attention(steps: Sequence[Step]) -> int:
    return sum(
        1
        for s in steps
        if s.action == "report" and needs_attention(s.placement.choice.policy, s.found.state)
    )


app.command(subscribe_command, name="subscribe")
app.command(unsubscribe_command, name="unsubscribe")
app.command(repos_command, name="repos")
app.command(items_command, name="items")
app.command(enable_command, name="enable")
app.command(disable_command, name="disable")
app.command(status_command, name="status")
app.command(diff_command, name="diff")
app.command(apply_command, name="apply")
app.command(sync_command, name="sync")
app.command(remove_command, name="remove")
