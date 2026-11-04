"""Settings, state rows and the source data blocks of the Ansible plugin."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Key, installed_code, key
from untaped.sdk.health import Retired
from untaped.sdk.storage import Cache, Dir, StateRows

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


class AnsibleSettings(BaseModel):
    """User-tunable profile settings."""

    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
        "stale_after_seconds": Retired(
            note="deleted in 12.0; use ansible.caches.source_data.max_age_seconds"
        ),
        "index_path": Retired(
            note="deleted in 12.0; ansible.index_dir names the folder that holds the index"
        ),
        "source_refresh_backend": Retired(
            note="deleted in 12.0; each repo's host probes its refs, else git ls-remote does"
        ),
        "probe_parallel": Retired(
            note="deleted in 12.0; use git.probe_parallel (live graph reads run 8 at once)"
        ),
        "source_refresh_rate_limit_floor": Retired(
            note="deleted in 12.0; the plugin that probes a host sets that host's budget floor"
        ),
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
            "Saved source `deps`, `impact`, `find` and `graph` use when no `--source` is given."
        ),
    )
    ref_scan_default: Literal["all", "default_branch"] = Field(
        default="all",
        description="Refs a source scans: `all` refs or each repo's default branch.",
    )
    git_fetch_parallel: int = Field(
        default=8, ge=1, le=32, description="Default `--parallel` of a source refresh."
    )
    source_refresh_repo_batch_size: int = Field(
        default=100,
        ge=1,
        description=(
            "Repos probed and committed per source refresh batch; a refresh pauses between "
            "batches once a host's probe budget is spent."
        ),
    )
    dependency_paths: list[str] = Field(
        default_factory=lambda: list(DEFAULT_DEPENDENCY_PATHS),
        description="Dependency files scanned in each repo.",
    )


class Alias(BaseModel):
    """A role or Galaxy name and the repository name it stands for."""

    model_config = ConfigDict(frozen=True)

    alias: Annotated[str, Key]
    repo: str


#: Role or Galaxy name to repository name aliases. Managed by `ansible source-alias` commands.
ALIASES = StateRows[Alias]()


def alias_map() -> dict[str, str]:
    """The saved aliases as a name → repository name map."""
    return {row.alias: row.repo for row in ALIASES.all()}


#: The dependency index: ``<ansible.index_dir>/index.v7.sqlite3``, or
#: ``plugins/ansible/index/`` under the home when the setting is unset. The
#: layout is ``sqlite_schema.ADDITIVE_SINCE`` (a test holds them equal;
#: importing the schema here would load the plugin's infrastructure at
#: startup), not the schema version: an additive schema change keeps sharing
#: the layout's file.
INDEX_DIR = Dir(at=key(AnsibleSettings).index_dir, layout=7, file="index.sqlite3")

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
