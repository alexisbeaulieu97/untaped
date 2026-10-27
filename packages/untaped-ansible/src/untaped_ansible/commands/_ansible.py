"""What every ansible command shares: its settings, github's, the index and the saved sources."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import ValidationError

from untaped.sdk import Command, UsageError, command_line, first_validation_error, not_found
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import SqliteDependencyIndex
from untaped_ansible.refresh import Backend, RefreshWiring
from untaped_ansible.settings import SOURCES, AnsibleSettings, SourceDefinition, alias_map
from untaped_github.api import GithubSettings, github_settings, github_web_host

#: How many hex characters of an inline source's definition hash name its key.
_FINGERPRINT_HEX_CHARS = 16

#: How ``--ref-scan-default`` reads in help, on ``source set`` and the inline selectors.
REF_SCAN_DEFAULT = "Scan strategy: all refs or only each repo's default branch."
BACKEND = "Ref probe backend for this refresh: auto, graphql, or git."
TEAM = (
    "GitHub team as ORG/SLUG; a bare SLUG is allowed when exactly one --org is given "
    "and normalizes to ORG/SLUG."
)

RefScanDefault = Literal["all", "default_branch"]


class AnsibleCommand(Command[AnsibleSettings]):
    """A command over the profile's ``ansible`` settings, its saved sources and its index."""

    @property
    def github(self) -> GithubSettings:
        """github's settings this run, which ansible reads through github's api."""
        return github_settings()

    @property
    def github_host(self) -> str | None:
        """The Git host of ``github.server.url``: the host a repo URL is read against."""
        return github_web_host(self.github.api_url)

    def wiring(
        self, index: SqliteDependencyIndex, *, parallel: int, backend: Backend | None
    ) -> RefreshWiring:
        """What refreshing any selected source needs, with ``parallel`` Git fetches."""
        return RefreshWiring(
            index=index,
            aliases=alias_map(),
            settings=self.settings,
            github_settings=self.github,
            http=self.ctx.http,
            plugin=self.ctx.plugin,
            warn=self.ctx.diag.warning,
            concurrency=parallel,
            backend=backend,
        )

    def saved(self, name: str) -> SourceDefinition:
        """The saved source ``name``; a missing one exits 1, naming the sources there are."""
        source = SOURCES.get(name)
        if source is None:
            raise AnsibleError(unknown_source(name), category="not_found")
        return source

    def warn_saved_sources_need_refresh(self) -> None:
        """Aliases are baked into cached source data at refresh time: say what to refresh."""
        names = [source.name for source in SOURCES.all()]
        if not names:
            return
        from untaped_ansible.commands.source import Refresh  # noqa: PLC0415 - import cycle

        commands = ", ".join(f"`{command_line(Refresh.spelled(name))}`" for name in names)
        self.ctx.diag.warning(
            "cached source data resolves aliases at refresh time; "
            f"run {commands} for this change to affect cached graphs",
        )


def unknown_source(name: str) -> str:
    return not_found("source", name, known=sorted(source.name for source in SOURCES.all()))


def source_definition(
    *,
    name: str,
    orgs: list[str],
    teams: list[str],
    repos: list[str],
    paths: list[str],
    ref_kinds: list[str],
    ref_patterns: list[str],
    ref_scan_default: RefScanDefault | None = None,
) -> SourceDefinition:
    """The source the selector flags describe; an invalid one exits 2 with the first problem."""
    try:
        return SourceDefinition(
            name=name,
            orgs=orgs,
            teams=teams,
            repos=repos,
            dependency_paths=paths,
            ref_kinds=ref_kinds,
            ref_patterns=ref_patterns,
            ref_scan_default=ref_scan_default,
        )
    except ValidationError as exc:
        raise UsageError(first_validation_error(exc)) from exc


def saved_source_key(name: str) -> str:
    """The index's key of the saved source ``name``."""
    return f"source:{name}"


def inline_source_key(source: SourceDefinition) -> str:
    """The index's key of inline selectors: a hash of the definition, so a rerun reuses it."""
    payload = source.model_dump(exclude={"name"}, exclude_none=True)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"inline:{hashlib.sha256(encoded).hexdigest()[:_FINGERPRINT_HEX_CHARS]}"
