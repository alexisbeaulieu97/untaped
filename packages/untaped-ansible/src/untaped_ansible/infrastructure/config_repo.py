"""The plugin's saved sources and aliases, read and written through its state rows.

Both live in ansible's own ``plugins/ansible/state.yml`` (``SOURCES`` and
``ALIASES``, held by the plugin class); these wrappers give the commands and
the refresh the shapes they read: aliases as a name → ``owner/repo`` map.
"""

from __future__ import annotations

from untaped_ansible.settings import ALIASES, SOURCES, Alias, SourceDefinition


class AliasRepository:
    """Read and write the aliases that map role or Galaxy names to GitHub repos."""

    def entries(self) -> dict[str, str]:
        return {row.alias: row.repo for row in ALIASES.all()}

    def set(self, alias: str, repo: str) -> None:
        ALIASES.put(Alias(alias=alias, repo=repo))

    def remove(self, alias: str) -> bool:
        return ALIASES.delete(alias) > 0


class SourceRepository:
    """Read and write the saved sources, one per name."""

    def entries(self) -> list[SourceDefinition]:
        return SOURCES.all()

    def get(self, name: str) -> SourceDefinition | None:
        return SOURCES.get(name)

    def upsert(self, source: SourceDefinition) -> None:
        SOURCES.put(source)

    def remove(self, name: str) -> bool:
        return SOURCES.delete(name) > 0
