"""Dotfiles adapters: git clones and source trees, the filesystem placer, and state."""

from untaped_dotfiles.infrastructure.git_repos import LocalGitRepos
from untaped_dotfiles.infrastructure.placer import FilesystemPlacer
from untaped_dotfiles.infrastructure.state_store import StateDotfilesStore
from untaped_dotfiles.infrastructure.trees import GitRefTree, WorkingTree

__all__ = [
    "FilesystemPlacer",
    "GitRefTree",
    "LocalGitRepos",
    "StateDotfilesStore",
    "WorkingTree",
]
