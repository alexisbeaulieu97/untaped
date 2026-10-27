"""The ``ansible`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`Ansible`, in this leaf
module: the class imports every command. Its docstring is the resolution
guide.
"""

from __future__ import annotations

from untaped.sdk import ExecutableCheck, Plugin, StubSkill
from untaped_ansible.commands.graph import Deps, Find, Graph, ImpactGroup
from untaped_ansible.commands.source import SourceGroup
from untaped_ansible.commands.source_alias import SourceAliasGroup
from untaped_ansible.settings import ALIASES, INDEX_DIR, SOURCE_DATA, SOURCES, AnsibleSettings


class Ansible(Plugin):
    """Analyze Ansible dependency graphs.

    Read when a deps, impact, find or graph result surprises you: which refs
    are read, how a target names a repo, where a walk stops, and which
    dependency files are skipped.

    ## Refs

    - `repo@v1` and `repo@main` are different nodes.
    - Local checkouts, and `find` roots given with a ref, are read as they
      are.
    - When the source has not scanned the default branch (a tags-only source),
      every cached ref is read, with a warning.
    - An unpinned dependency resolves to its default-branch node: the source's
      recorded default branch, or GitHub's for live reads. With no known default
      branch the node stays ref-less; a tags-only source stops there with a "ref
      is not cached" warning.
    - `impact --ref R` (and `graph --ref R --direction up`) includes unpinned
      dependents when `R` is the target's cached default branch. If that branch
      is unknown, a warning counts the unpinned dependents left out.

    ## Repos and local targets

    - Repo ids match in any case (`Acme/Base` is `acme/base`).
    - URLs on the host of `github.server.url` resolve as `github.com` URLs do.
      Path-like sources in requirements files, such as `./local`, stay
      unresolved.
    - A local path's Git remote is read at the top level of a checkout, linked
      worktrees included.
    - Without a source, a local target is read live. With no GitHub token it
      stays offline and warns that its transitive dependencies were not read.

    ## Where a walk stops

    - A row's `stopped` field says why a repo's own edges were not read. When
      `--depth` left repos unread, one `hint:` on stderr says how many (per root
      for `find`).
    - A live read that fails for one repo (a deleted repo or tag, lost access)
      is a warning and leaves that node unread. A rejected token or a rate limit
      stops the command instead.
    - Collections in requirements files are not followed. A source refresh and
      a local target print one warning listing them; live GitHub reads do not.
    - Cycles are found only within the depth that was walked.

    ## Skipped dependency files

    - Malformed, templated or wrong-shape dependency files are skipped with a
      warning and never fail the command. A `dependencies`, `roles` or
      `collections` section that is present but not a list warns and is
      skipped; empty files and missing or empty sections are silent.
    - Live warnings name `repo@ref path`. `source refresh` prints the skipped
      files of that run only; they are not kept with the cached data.
    """

    settings = AnsibleSettings
    commands = (SourceGroup, SourceAliasGroup, Deps, ImpactGroup, Find, Graph)
    sources = SOURCES
    aliases = ALIASES
    index = INDEX_DIR
    source_data = SOURCE_DATA
    git = ExecutableCheck("git", purpose="git-backed source refresh")
    skill = StubSkill(
        "Ansible role and project dependencies across GitHub repositories: what a role "
        "depends on, what depends on it and which of its variables they use, which "
        "projects reach a repo, and dependency graphs. Use when the work concerns who "
        "uses a role, the impact of changing one, or requirements.yml or meta/main.yml "
        "dependencies."
    )


__all__ = ["Ansible"]
