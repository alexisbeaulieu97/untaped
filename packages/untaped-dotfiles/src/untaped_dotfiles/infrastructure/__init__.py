"""Dotfiles adapters: git clones and source trees, the filesystem placer, and state."""

from untaped_dotfiles.infrastructure.git_repos import LocalGitRepos
from untaped_dotfiles.infrastructure.placer import FilesystemPlacer, link_hash
from untaped_dotfiles.infrastructure.state_store import StateDotfilesStore, item_id
from untaped_dotfiles.infrastructure.trees import GitRefTree, WorkingTree

__all__ = [
    "FilesystemPlacer",
    "GitRefTree",
    "LocalGitRepos",
    "StateDotfilesStore",
    "WorkingTree",
    "item_id",
    "link_hash",
]
