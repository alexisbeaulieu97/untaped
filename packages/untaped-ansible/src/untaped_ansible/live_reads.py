"""The live reads a graph command makes when no source answers: :class:`LiveReads`.

Each repo is located over the plugins that list repos, and read through the
repo store (:class:`~untaped_ansible.infrastructure.StoreDependencyIndex`).
"""

from __future__ import annotations

from collections.abc import Callable

from untaped.sdk import PluginHandle
from untaped_ansible.application.ports import DependencyIndex
from untaped_ansible.domain.identity import IdentityResolver
from untaped_ansible.infrastructure import GitSourceStore, StoreDependencyIndex
from untaped_ansible.settings import AnsibleSettings
from untaped_git.api import RepoFinder, default_branch, ls_remote

#: Repos a live build reads at once.
_LIVE_READ_PARALLEL = 8


class LiveReads:
    """One live read index shared by every root of a graph command.

    It is made on first use, so a command that reads nothing live asks no
    provider, and each repo/ref is read once per command however many
    ``find`` roots reach it.
    """

    def __init__(
        self,
        *,
        plugin: PluginHandle,
        wrapped: DependencyIndex,
        resolver: Callable[[], IdentityResolver],
        settings: AnsibleSettings,
    ) -> None:
        self._plugin = plugin
        self._wrapped = wrapped
        self._resolver = resolver
        self._settings = settings
        self._index: StoreDependencyIndex | None = None

    def index(self) -> StoreDependencyIndex:
        if self._index is None:
            self._index = StoreDependencyIndex(
                locate=RepoFinder(offer_url=False).resolve,
                remote_refs=ls_remote,
                default_branch=default_branch,
                store=GitSourceStore(plugin=self._plugin),
                wrapped=self._wrapped,
                resolver=self._resolver(),
                dependency_paths=self._settings.dependency_paths,
                concurrency=_LIVE_READ_PARALLEL,
            )
        return self._index
