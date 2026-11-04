"""``untaped dotfiles subscribe|unsubscribe``: the sources this machine reads packages from."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from untaped.sdk import (
    Confirm,
    Document,
    DryRun,
    Output,
    Positional,
    UntapedError,
    UsageError,
    key,
    plural,
    q,
    run_hint,
)
from untaped_dotfiles.catalog import Offer, load_packages
from untaped_dotfiles.commands._dotfiles import DotfilesCommand, Services, package_row
from untaped_dotfiles.errors import SourceNotFoundError
from untaped_dotfiles.package import PackageFile
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.records import ApplyOutcome, Package
from untaped_dotfiles.settings import DotfilesSettings, PackageChoice, PackageSource
from untaped_dotfiles.sources import Source, check_name, classify, name_from, resolve_url
from untaped_dotfiles.trees import WorkingTree

SOURCE_HELP = (
    "A local folder (starts with `/`, `~`, `.` or a drive), a git URL (`://` or "
    "`git@host:path`), or a repo name a repo source lists (`owner/name`)."
)


class Subscribe(DotfilesCommand):
    """Subscribe to a source of packages and list them; `--add` turns packages on.

    SOURCE is told apart by how it starts, never by what exists here: a
    folder starts with `/`, `~`, `.` or a drive (`C:\\`), a git URL has `://`
    or is scp-like (`git@host:path`), and anything else is a repo name a
    repo source lists (a folder `owner/repo` here is `./owner/repo`).

    A git source gets a clone under `dotfiles.clones_dir`, through the git
    plugin's repo store (its credentials and proxy); a local folder is read as
    it stands and never fetched, so its links point into it. Nothing is
    placed: `apply` places the packages that are on. A package file that
    doesn't load is a row with `error` (exit 1); the source stays subscribed,
    so a fix in the source and the next `apply` recover.
    """

    source: Annotated[Positional[str], Document(PackageFile)] = Field(description=SOURCE_HELP)
    name: str | None = Field(
        None, description="The source's name. Default: SOURCE's last segment, without `.git`."
    )
    ref: str | None = Field(
        None, description="The branch to follow (a git source). Default: the remote's default."
    )
    add: list[str] = Field(
        default_factory=list, description="Turn this package of the source on (repeatable)."
    )
    dry_run: DryRun
    output: Output[Package]

    def run(self) -> Iterator[Package]:
        kind = classify(self.source)
        folder = Path(self.source).expanduser().resolve() if kind == "folder" else None
        name = check_name(self.name or name_from(str(folder or self.source)))
        if name in self.settings.sources:
            raise UsageError(
                f"source {q(name)} is already subscribed",
                hint=f"pass --name, or {run_hint(Unsubscribe.spelled(name))} first",
            )
        if folder is not None:
            if self.ref is not None:
                raise UsageError("--ref applies to a git source")
            if not folder.is_dir():
                raise UsageError(f"{folder} is not a folder")
            spec = PackageSource(path=folder)
        else:
            spec = PackageSource(url=self.source, ref=self.ref)
        svc = self.services()
        with svc.store.locked():
            if spec.path is not None:
                source = Source(name=name, spec=spec, root=spec.path, tree=WorkingTree(spec.path))
                rows = self._subscribe(svc, source, said=f"local folder {spec.path}")
            else:
                rows = self._clone_and_subscribe(svc, name, spec)
        for row in rows:
            if row.error is not None:
                self.ctx.diag.failure(row.error, item=f"{row.source}/{row.name}")
        self.output.empty("The source has no packages under .untaped/dotfiles/.")
        return iter(rows)

    def _clone_and_subscribe(self, svc: Services, name: str, spec: PackageSource) -> list[Package]:
        assert spec.url is not None
        url = resolve_url(spec.url)
        if self.dry_run:
            tree, branch, done = svc.clones.peek(url, spec.ref)
            try:
                source = Source(name=name, spec=spec, root=svc.clones.folder(name), tree=tree)
                return self._subscribe(svc, source, said=f"at {branch} ({(tree.commit or '')[:7]})")
            finally:
                done()
        left = svc.store.get_clone(name)
        if left is not None:  # a clone left behind by a source deleted from config.yml by hand
            svc.clones.release(left)
            svc.store.remove_clone(name)
        record = svc.clones.make(name, spec.url, url, ref=spec.ref)
        svc.store.put_clone(record)
        try:
            tree = svc.clones.tree(record)
            source = Source(name=name, spec=spec, root=Path(record.folder), tree=tree, clone=record)
            return self._subscribe(svc, source, said=f"at {record.ref} ({(tree.commit or '')[:7]})")
        except BaseException:
            svc.clones.release(record)
            svc.store.remove_clone(name)
            raise

    def _subscribe(self, svc: Services, source: Source, *, said: str) -> list[Package]:
        loaded = load_packages(source, self.ctx.plugin.committed("dotfiles").as_posix())
        names = [pkg for pkg, _, _ in loaded]
        offers = {
            pkg: Offer(source, pkg, package, error, PackageChoice() if pkg in self.add else None)
            for pkg, package, error in sorted(loaded, key=lambda each: each[0])
        }
        for wanted in dict.fromkeys(self.add):
            offer = offers.get(wanted)
            if offer is None:
                raise UsageError(
                    f"source {q(source.name)} has no package {q(wanted)}",
                    hint=f"its packages: {', '.join(names)}" if names else None,
                )
            if offer.error is not None:
                raise offer.error
            excluded = offer.exclusion(svc.inventory.machine)
            if excluded is not None:
                raise UsageError(f"{offer.spelled} doesn't apply on this machine: {excluded}")
        entry = source.spec.model_copy(
            update={"packages": {pkg: PackageChoice() for pkg in dict.fromkeys(self.add)}}
        )
        plan = self.ctx.plugin.plan_settings({key(DotfilesSettings).sources[source.name]: entry})
        rows = [package_row(offer, svc.inventory.machine) for offer in offers.values()]
        if not self.dry_run.go_ahead(preview=plan.preview):
            return rows
        plan.write()
        on = sum(1 for offer in offers.values() if offer.on)
        self.ctx.diag.success(
            f"subscribed {q(source.name)} {said}: {plural(len(offers), 'package')}, {on} on"
        )
        from untaped_dotfiles.commands.apply import Apply  # noqa: PLC0415 - a cycle
        from untaped_dotfiles.commands.packages import Add  # noqa: PLC0415 - a cycle

        for offer in offers.values():
            if offer.on and _author_sync_steps(offer):
                self.ctx.diag.info(
                    f"{offer.spelled} suggests sync and has steps; a step the author adds or"
                    " changes later waits for you unless this machine chooses the policy:"
                    f" {run_hint(Add.spelled(offer.spelled, '--policy', 'sync'))}"
                )
        self.ctx.diag.hint(run_hint(Apply.spelled() if on else Add.spelled("PKG")))
        return rows


class Unsubscribe(DotfilesCommand):
    """Take back every file a source's packages placed, then forget the source and its clone.

    Links and unedited copies are deleted, merged keys taken back out, and a
    file edited on this machine kept aside. `--keep-files` forgets them and
    leaves the files; a link into the clone becomes a copy of what it showed,
    since the clone goes. It works from untaped's own records too, so a source
    deleted from `config.yml` by hand can still be taken back. A file that
    can't be taken back fails its row (exit 1) and the source stays. Steps and
    working copies are forgotten: nothing a step did is undone, and a working
    copy stays.
    """

    source: Positional[str] = Field(description="The subscribed source's name.")
    keep_files: bool = Field(False, description="Leave the placed files where they are.")
    confirm: Confirm
    output: Output[ApplyOutcome]

    def run(self) -> Iterator[ApplyOutcome]:
        name = self.source
        svc = self.services()
        with svc.store.locked():
            in_config = name in self.settings.sources
            clone = svc.store.get_clone(name)
            records = sorted(
                (r for r in svc.store.placed() if r.source == name), key=lambda r: r.target
            )
            if not (in_config or clone is not None or records):
                raise SourceNotFoundError(
                    f"no source {q(name)} is subscribed",
                    hint=f"subscribed: {', '.join(self.settings.sources)}"
                    if self.settings.sources
                    else None,
                )
            steps = [*svc.steps.forgetting([(name,)]), *svc.repos.forgetting([(name,)])]
            planned = [
                *svc.applier.removal(
                    records, keep_files=self.keep_files, clone=clone if self.keep_files else None
                ),
                *steps,
            ]
            changes = {key(DotfilesSettings).sources[name]: None} if in_config else {}
            plan = self.ctx.plugin.plan_settings(changes) if changes else None
            taking = (
                ""
                if self.keep_files or not records
                else f"take back {plural(len(records), 'file')} and "
            )
            if not self.confirm(
                f"{taking}unsubscribe {q(name)}?".capitalize(),
                preview=_preview(planned, plan.preview if plan is not None else None),
            ):
                return iter(planned)
            rows = svc.applier.take_back(
                records, keep_files=self.keep_files, clone=clone if self.keep_files else None
            )
            if any(row.failed for row in rows):
                self.report_rows(rows)
                self.ctx.diag.warning(f"{q(name)} stays subscribed until every file is taken back")
                return iter(rows)
            if plan is not None:
                plan.write()
            if clone is not None:
                try:
                    svc.clones.release(clone)
                except UntapedError as exc:
                    self.ctx.diag.failure(exc, item=name)
                svc.store.remove_clone(name)
            rows += svc.steps.forgetting([(name,)], done=True)
            rows += svc.repos.forgetting([(name,)], done=True)
            svc.store.forget_applied((name,))
            svc.store.forget_repos((name,))
            remaining = {k: v for k, v in self.settings.sources.items() if k != name}
            self.services(self.settings.model_copy(update={"sources": remaining})).refresh_status()
        self.ctx.diag.success(f"unsubscribed {q(name)}")
        return iter(rows)


def _preview(
    rows: list[ApplyOutcome], settings: Callable[[], list[Any]] | None
) -> Callable[[], list[Any]]:
    """The rows (when any), then the settings lines, for ``Confirm(preview=)``."""

    def preview() -> list[Any]:
        return [*([rows] if rows else []), *(settings() if settings is not None else [])]

    return preview


def _author_sync_steps(offer: Offer) -> bool:
    """Whether the author suggests ``sync`` for a package with steps (they follow only a
    policy this machine chooses)."""
    package = offer.package
    return (
        package is not None
        and package.policy is Policy.SYNC
        and any(item.steps for item in package.items.values())
    )
