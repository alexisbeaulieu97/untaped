"""Settings, state rows and the source data blocks of the Ansible plugin."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from untaped.sdk import Cache, Dir, Key, Retired, StateRows, installed_code, key
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

    name: Annotated[str, Key]
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
        "stale_after": Retired(
            note="deleted in 11.0; use ansible.caches.source_data.max_age_seconds"
        ),
        "stale_after_seconds": Retired(
            note="deleted in 12.0; use ansible.caches.source_data.max_age_seconds"
        ),
        "index_path": Retired(
            note="deleted in 12.0; ansible.index_dir names the folder that holds the index"
        ),
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

    index_dir: Path | None = Field(
        default=None,
        description=(
            "Folder of the SQLite index of refreshed source data, an absolute path; the "
            "file in it is `index.v<layout>.sqlite3`, one per layout. Unset: "
            "`plugins/ansible/index/` under the untaped home."
        ),
    )
    default_source: str | None = Field(
        default=None,
        description=(
            "Saved source `deps`, `impact`, `find` and `graph` use when no "
            "`--source` or inline selector is given."
        ),
    )
    ref_scan_default: Literal["all", "default_branch"] = Field(
        default="all",
        description="Refs a source scans: `all` refs or each repo's default branch.",
    )
    source_refresh_backend: Literal["auto", "graphql", "git"] = Field(
        default="auto", description="Ref probe backend for source refresh."
    )
    git_fetch_parallel: int = Field(
        default=8, ge=1, le=32, description="Default `--parallel` of a source refresh."
    )
    probe_parallel: int = Field(
        default=8, ge=1, le=32, description="Concurrent ref probes during source refresh."
    )
    source_refresh_repo_batch_size: int = Field(
        default=100, ge=1, description="Repos committed per source refresh batch."
    )
    source_refresh_rate_limit_floor: int = Field(
        default=500,
        ge=0,
        description="Stop a refresh (resumable) when the GraphQL budget drops below this.",
    )
    dependency_paths: list[str] = Field(
        default_factory=lambda: list(DEFAULT_DEPENDENCY_PATHS),
        description="Dependency files scanned in each repo.",
    )


class Alias(BaseModel):
    """A role or Galaxy name and the GitHub ``owner/repo`` it stands for."""

    model_config = ConfigDict(frozen=True)

    alias: Annotated[str, Key]
    repo: str


#: Saved sources, one row per name. Managed by `ansible source` commands.
SOURCES = StateRows[SourceDefinition](omit_none=True)
#: Role or Galaxy name to `owner/repo` aliases. Managed by `ansible source-alias` commands.
ALIASES = StateRows[Alias]()

#: The dependency index: ``<ansible.index_dir>/index.v4.sqlite3``, or
#: ``plugins/ansible/index/`` under the home when the setting is unset. The
#: layout is ``sqlite_schema.ADDITIVE_SINCE`` (a test holds them equal;
#: importing the schema here would load the plugin's infrastructure at
#: startup), not the schema version: an additive schema change keeps sharing
#: the layout's file.
INDEX_DIR = Dir(at=key(AnsibleSettings).index_dir, layout=4, file="index.sqlite3")

#: The version of what a ref's dependency edges are made by: the parser and
#: identity code, the models they build and PyYAML. Folded into the dependency
#: paths fingerprint, so an edit to any of them (or a PyYAML upgrade) re-parses
#: every ref: there is no number to forget to bump.
parser_version = installed_code(
    "untaped_ansible.domain.parser",
    "untaped_ansible.domain.identity",
    "untaped_ansible.domain.models",
    "yaml",
)

#: The version of the variable analysis: its modules, Jinja2 and PyYAML (its
#: files, so whether libyaml is built in counts too). The analyser imports
#: nothing first-party but its data types (a test holds it), so these are
#: everything that decides its facts.
analyser_version = installed_code(
    "untaped_ansible.domain.variables",
    "untaped_ansible.domain.variable_models",
    "jinja2",
    "yaml",
)


def _index_version() -> str:
    return f"{parser_version()}:{analyser_version()}"


#: Each source's refs, edges and variable facts in the index, refreshed past
#: a day, and again when the code that filled a source (the dependency parser
#: or the variable analysis) changed. The plugin class holds it as
#: ``source_data``; setting: ``ansible.caches.source_data.max_age_seconds``.
SOURCE_DATA = Cache(
    label="source data", max_age=timedelta(days=1), version=_index_version, follows=INDEX_DIR
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
