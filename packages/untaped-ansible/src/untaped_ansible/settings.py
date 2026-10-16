"""Settings and state models for the Ansible plugin."""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from untaped.sdk import Cache, Retired, Store, code_version, setting_guard
from untaped_github.api import normalize_team_scopes

DEFAULT_DEPENDENCY_PATHS = (
    "roles/requirements.yml",
    "roles/requirements.yaml",
    "requirements.yml",
    "requirements.yaml",
    "meta/requirements.yml",
    "meta/requirements.yaml",
    "meta/main.yml",
    "meta/main.yaml",
)
ALLOWED_REF_KINDS = ("heads", "tags")


class SourceDefinition(BaseModel):
    """Named GitHub search boundary for index refresh and impact queries."""

    model_config = ConfigDict(frozen=True)

    name: str
    orgs: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    repos: list[str] = Field(default_factory=list)
    dependency_paths: list[str] = Field(default_factory=list)
    ref_kinds: list[str] = Field(default_factory=list)
    ref_patterns: list[str] = Field(default_factory=list)
    ref_scan_default: Literal["all", "default_branch"] | None = None

    @field_validator("orgs", "repos", "dependency_paths", "ref_kinds", "ref_patterns")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        return _dedupe_sorted(values)

    @field_validator("teams")
    @classmethod
    def _normalize_teams(cls, teams: list[str], info: ValidationInfo) -> list[str]:
        return normalize_team_refs(_dedupe_sorted(teams), info.data.get("orgs", []))

    @model_validator(mode="after")
    def _validate_source(self) -> Self:
        if not any((self.orgs, self.teams, self.repos)):
            raise ValueError("source requires --org, --team, or --repo")
        for repo in self.repos:
            if not is_repo_name(repo):
                raise ValueError(f"repo must be owner/name: {repo!r}")
        invalid_ref_kinds = sorted(set(self.ref_kinds) - set(ALLOWED_REF_KINDS))
        if invalid_ref_kinds:
            raise ValueError("ref-kind must be heads or tags")
        return self


class AnsibleSettings(BaseModel):
    """User-tunable profile settings."""

    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
        "stale_after": Retired(note="deleted in 11.0; use ansible.caches.index.max_age_seconds"),
        "repo_cache_path": "cache_dir",
        "cache_dir": Retired(note="deleted in 11.0; the repo store lives under git.store_dir"),
        "git_fetch_depth": Retired(note="deleted in 11.0; the repo store fetches full history"),
        "git_blob_filter": Retired(note="deleted in 11.0; the repo store is blobless"),
        "git_clone_protocol": Retired(
            note="deleted in 11.0; set github.git_protocol: ssh to keep ssh"
        ),
        "git_fetch_concurrency": "git_fetch_parallel",
        "probe_concurrency": "probe_parallel",
    }

    model_config = ConfigDict(frozen=True)

    index_path: Path = Path("~/.untaped/plugins/ansible/index.sqlite3")
    default_source: str | None = None
    ref_scan_default: Literal["all", "default_branch"] = "all"
    source_refresh_backend: Literal["auto", "graphql", "git"] = "auto"
    git_fetch_parallel: int = Field(default=8, ge=1, le=32)
    probe_parallel: int = Field(default=8, ge=1, le=32)
    source_refresh_repo_batch_size: int = Field(default=100, ge=1)
    source_refresh_rate_limit_floor: int = Field(default=500, ge=0)
    dependency_paths: list[str] = Field(default_factory=lambda: list(DEFAULT_DEPENDENCY_PATHS))


class AnsibleState(BaseModel):
    """The Ansible plugin's state: its saved sources and aliases."""

    model_config = ConfigDict(frozen=True)

    sources: list[SourceDefinition] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)


def default_index_path() -> Path:
    """``ansible.index_path``'s declared default: the base the index's layouts sit beside."""
    default: Path = AnsibleSettings.model_fields["index_path"].default
    return default.expanduser()


#: The dependency index file at the default path, one file per layout
#: (``index.v4.sqlite3``). The layout is ``sqlite_schema.ADDITIVE_SINCE``
#: (a test holds them equal; importing the schema here would load the
#: plugin's infrastructure at startup), not the schema version: an additive
#: schema change keeps sharing the layout's file. ``bare=4``: 11.0 kept
#: layout 4 at ``index.sqlite3``, renamed on first open. A layout any
#: profile points ``ansible.index_path`` at is that profile's index: it stays.
INDEX_PATH_GUARD = setting_guard("ansible", "index_path")
INDEX_STORE = Store(
    "ansible", "index", base=default_index_path, layout=4, bare=4, keep=INDEX_PATH_GUARD
)


def parser_version() -> str:
    """The version of what a ref's dependency edges are made by: the parser and identity code.

    Folded into the dependency paths fingerprint, so an edit to the parser,
    identity or the models they build (or a PyYAML upgrade) re-parses every
    ref: there is no number to forget to bump. The parser uses PyYAML's pure
    Python loader, so libyaml does not count.
    """
    return ":".join(
        (
            code_version("untaped_ansible.domain.parser", distributions=("PyYAML",)),
            code_version("untaped_ansible.domain.identity"),
            code_version("untaped_ansible.domain.models"),
        )
    )


def analyser_version() -> str:
    """The version of the variable analysis: its modules, jinja2 and PyYAML (with libyaml or not).

    The analyser imports nothing first-party but its data types (a test
    holds it), so the two modules' text and those libraries are everything
    that decides its facts.
    """
    return ":".join(
        (
            code_version(
                "untaped_ansible.domain.variables",
                distributions=("jinja2", "PyYAML"),
                extra=(_libyaml(),),
            ),
            code_version("untaped_ansible.domain.variable_models"),
        )
    )


def _libyaml() -> str:
    """Whether PyYAML's C loader is present, found without importing it."""
    return f"libyaml={importlib.util.find_spec('yaml._yaml') is not None}"


def _index_version() -> str:
    return f"{parser_version()}:{analyser_version()}"


#: The dependency index: each source's refs, edges and variable facts,
#: refreshed past a day, and again when the code that filled a source
#: (the dependency parser or the variable analysis) changed.
INDEX = Cache(
    "ansible",
    "index",
    label="source data",
    max_age=timedelta(days=1),
    renamed_from=("stale_after_seconds",),
    removed_in=12,
    version=_index_version,
    store=INDEX_STORE,
)


def normalize_team_refs(teams: list[str], orgs: list[str]) -> list[str]:
    """Expand bare team slugs when the source has one unambiguous org."""
    return [f"{scope.org}/{scope.slug}" for scope in normalize_team_scopes(teams, orgs=tuple(orgs))]


def _dedupe_sorted(values: list[str]) -> list[str]:
    return sorted(dict.fromkeys(values))


def is_repo_name(value: str) -> bool:
    """Return whether ``value`` is a GitHub ``owner/name`` repo id."""
    owner, separator, repo = value.partition("/")
    return bool(owner and separator and repo and "/" not in repo)
