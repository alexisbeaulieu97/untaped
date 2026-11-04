"""What every ansible command shares: its settings, the index and the saved sources."""

from __future__ import annotations

import shlex
from typing import Literal

from untaped.sdk import Command, command_line, not_found
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import SqliteDependencyIndex
from untaped_ansible.refresh import RefreshWiring
from untaped_ansible.settings import AnsibleSettings, alias_map
from untaped_ansible.sources import SOURCES, SourceDefinition

#: How ``--ref-scan-default`` reads in help, on ``source set`` and ``source patch``.
REF_SCAN_DEFAULT = "Scan strategy: all refs or only each repo's default branch."

RefScanDefault = Literal["all", "default_branch"]


class AnsibleCommand(Command[AnsibleSettings]):
    """A command over the profile's ``ansible`` settings, its saved sources and its index."""

    def wiring(self, index: SqliteDependencyIndex, *, parallel: int) -> RefreshWiring:
        """What refreshing any selected source needs, with ``parallel`` Git fetches."""
        return RefreshWiring(
            index=index,
            aliases=alias_map(),
            settings=self.settings,
            plugin=self.ctx.plugin,
            warn=self.ctx.diag.warning,
            concurrency=parallel,
        )

    def saved(self, name: str) -> SourceDefinition:
        """The saved source ``name``; a missing one exits 1, naming the sources there are."""
        source = SOURCES.get(name)
        if source is None:
            raise AnsibleError(unknown_source(name), category="not_found")
        return source

    def warn_saved_sources_need_refresh(self) -> None:
        """Aliases are baked into cached source data at refresh time: say what to refresh."""
        names = SOURCES.keys()  # a damaged row still needs its refresh named
        if not names:
            return
        from untaped_ansible.commands.source import Refresh  # noqa: PLC0415 - import cycle

        lines = (command_line(Refresh.spelled(shlex.quote(name))) for name in names)
        commands = ", ".join(f"`{line}`" for line in lines)
        self.ctx.diag.warning(
            "cached source data resolves aliases at refresh time; "
            f"run {commands} for this change to affect cached graphs",
        )


def unknown_source(name: str) -> str:
    return not_found("source", name, known=sorted(SOURCES.keys()))


def saved_source_key(name: str) -> str:
    """The index's key of the saved source ``name``."""
    return f"source:{name}"
