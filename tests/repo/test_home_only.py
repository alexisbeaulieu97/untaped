"""Every first-party setting that locates data shared by every project is home-only.

A project's ``.untaped/config.yml`` or a profile that set one would split that
data (a second repo store, an index of its own), so the layer refuses it. A
setting a ``Dir(at=...)`` places is home-only without a mark. A new path
setting decides: placed by a ``Dir`` or marked ``HomeOnly()``, or listed here
with why a project may set it.
"""

from __future__ import annotations

from pathlib import PurePath

import pytest

from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.runtime.settings import get_settings_model
from untaped.sdk._schema import walk_settings

pytestmark = pytest.mark.usefixtures("fresh_composition", "_isolated_config")

HOME_ONLY = frozenset(
    {
        "ansible.index_dir",
        "dotfiles.clones_dir",
        "dotfiles.kept_dir",
        "dotfiles.os",  # this machine's OS: one answer per machine, whatever the folder
        "dotfiles.state_dir",
        "git.store_dir",
        "recipe.library_dir",
        "workspace.workspaces_dir",
    }
)

#: Path settings a project may set, and why each locates nothing shared.
PROJECT_PATHS = {
    "untaped.http.ca_bundle_path": (
        "trust for this folder's requests; a credentialed client refuses one a project set"
    ),
}


def test_every_shared_folder_setting_is_home_only(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    composed = bootstrap.compose_root(candidates=first_party_candidates)
    assert composed.quarantine == ()
    descriptors = walk_settings(get_settings_model())
    assert {d.key for d in descriptors if d.home_only} == HOME_ONLY
    paths = {
        d.key
        for d in descriptors
        if isinstance(d.annotation, type) and issubclass(d.annotation, PurePath)
    }
    assert paths - HOME_ONLY == set(PROJECT_PATHS), (
        "a new path setting: place a Dir(at=) with it, or mark it HomeOnly(), when it locates "
        "data every project shares, or list it in PROJECT_PATHS with why a project may set it"
    )
