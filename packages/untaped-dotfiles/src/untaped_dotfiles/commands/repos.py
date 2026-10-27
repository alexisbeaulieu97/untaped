"""``untaped dotfiles subscribe|unsubscribe|repos``: the repos a machine follows."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Confirm, Output, Positional, plural, q, run_hint
from untaped_dotfiles.commands._dotfiles import DotfilesCommand, utc_now
from untaped_dotfiles.records import Item, Repo, RepoOutcome
from untaped_dotfiles.subscriptions import SubscribeRepo, UnsubscribeRepo


class Subscribe(DotfilesCommand):
    """Subscribe to a repo: clone a URL under dotfiles.repos_dir, or register a checkout.

    Nothing is enabled yet: the manifest's items are listed, with the policy
    each suggests and why one does not apply on this machine, and `enable`
    picks them. A registered checkout is fetched but never pulled: its link
    files show whatever its working tree holds, and `repos` shows it behind.

    Git runs without prompting for credentials, so a private repo needs a
    credential helper or an SSH agent.
    """

    source: Positional[str] = Field(description="A git URL to clone, or the path of a checkout.")
    name: str | None = Field(
        None, description="Repo name. Default: the last path segment of SOURCE."
    )
    manifest: str | None = Field(
        None, description="Manifest path inside the repo. Default: dotfiles.yml."
    )
    ref: str | None = Field(
        None, description="Branch to follow. Default: the repo's default branch."
    )
    output: Output[Item]

    def run(self) -> Iterator[Item]:
        svc = self.services()
        subscribe = SubscribeRepo(
            svc.store, svc.git, svc.inventory, repos_dir=svc.repos_dir, now=utc_now
        )
        record = subscribe(self.source, name=self.name, manifest=self.manifest, ref=self.ref)
        rows = svc.catalog.rows(record.name)
        self.output.empty("No items in the manifest.")
        self.ctx.diag.success(f"subscribed {q(record.name)} ({plural(len(rows), 'item')})")
        self.ctx.diag.hint(run_hint("dotfiles enable ITEMS"))
        return iter(rows)


class Unsubscribe(DotfilesCommand):
    """Forget a repo. Refuses while any of its items is enabled."""

    name: Positional[str] = Field(description="The subscribed repo's name.")
    delete_clone: bool = Field(
        False, description="Also delete the clone the tool made (never a registered checkout)."
    )
    confirm: Confirm
    output: Output[RepoOutcome]

    def run(self) -> RepoOutcome:
        unsubscribe = UnsubscribeRepo(self.services().store)
        record = unsubscribe.plan(self.name)
        what = f"forget repo {q(self.name)}" + (
            " and delete its clone" if self.delete_clone else ""
        )
        if not self.confirm(f"{what.capitalize()}?"):
            return RepoOutcome(name=self.name, action="planned", detail=what)
        row = unsubscribe(record, delete_clone=self.delete_clone)
        self.ctx.diag.success(f"unsubscribed {q(self.name)}")
        return row


class Repos(DotfilesCommand):
    """List subscribed repos with their branch and how far behind origin they are."""

    output: Output[Repo]

    def run(self) -> Iterator[Repo]:
        self.output.empty("No repos subscribed.")
        return iter(self.services().reader.repo_rows())
