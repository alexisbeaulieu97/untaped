"""``untaped ansible source-alias``: map role or Galaxy names to the GitHub repos they are."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Confirm, Group, Output, Positional, UsageError, not_found, q
from untaped_ansible.commands._ansible import AnsibleCommand
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import AliasRepository
from untaped_ansible.records import SourceAlias, SourceAliasOutcome
from untaped_ansible.settings import ALIASES, is_repo_name


class Set(AnsibleCommand):
    """Map an Ansible role/Galaxy name to a GitHub owner/repo (creates or replaces).

    Requirements files name a role by that name; the alias says which repo it
    is. Aliases apply when a source refreshes, so run `source refresh` after
    changing one.

    Examples
    --------
    ```console
    $ untaped ansible source-alias set acme.base acme/ansible-role-base
    ```
    """

    alias: Positional[str] = Field(description="Alias to set.")
    repo: Positional[str] = Field(description="Canonical GitHub owner/repo.")
    output: Output[SourceAliasOutcome]

    def run(self) -> SourceAliasOutcome:
        if not is_repo_name(self.repo):
            raise UsageError(f"alias target must be a GitHub owner/name repo (got {q(self.repo)})")
        aliases = AliasRepository()
        previous = aliases.entries().get(self.alias)
        action = "unchanged" if previous == self.repo else "updated" if previous else "created"
        if action != "unchanged":
            aliases.set(self.alias, self.repo)
            self.warn_saved_sources_need_refresh()
        return SourceAliasOutcome(action=action, alias=self.alias, repo=self.repo)


class List(AnsibleCommand):
    """List source aliases."""

    output: Output[SourceAlias]

    def run(self) -> Iterator[SourceAlias]:
        self.output.empty(
            "No source aliases configured. Map one with "
            "`untaped ansible source-alias set NAME OWNER/REPO`."
        )
        for alias, repo in sorted(AliasRepository().entries().items()):
            yield SourceAlias(alias=alias, repo=repo)


class Remove(AnsibleCommand):
    """Remove a source alias.

    Confirms first; without a terminal it needs --yes.
    """

    removes = ALIASES

    alias: Positional[str] = Field(description="Alias to remove.")
    confirm: Confirm
    output: Output[SourceAliasOutcome]

    def run(self) -> SourceAliasOutcome:
        aliases = AliasRepository()
        entries = aliases.entries()
        repo = entries.get(self.alias)
        if repo is None:
            raise AnsibleError(
                not_found("source alias", self.alias, known=sorted(entries)), category="not_found"
            )
        if not self.confirm(f"Remove source alias {q(self.alias)} -> {repo}?"):
            return SourceAliasOutcome(action="planned", alias=self.alias, repo=repo)
        aliases.remove(self.alias)
        self.warn_saved_sources_need_refresh()
        return SourceAliasOutcome(action="deleted", alias=self.alias, repo=repo)


class SourceAliasGroup(Group):
    """Map role or Galaxy names in requirements files to GitHub repos."""

    commands = (Set, List, Remove)
