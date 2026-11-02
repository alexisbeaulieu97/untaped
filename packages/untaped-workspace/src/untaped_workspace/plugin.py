"""The ``workspace`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`WorkspacePlugin`, in this
leaf module: the class imports every command, so ``api`` (which repo
providers import) imports without them. Its docstring is the workspace guide.
"""

from __future__ import annotations

from untaped.sdk import Plugin, StubSkill, experimental
from untaped_workspace.api import RepoSource
from untaped_workspace.commands.archive import Archive, Remove
from untaped_workspace.commands.create import Add, Create
from untaped_workspace.commands.repos import ReposGroup
from untaped_workspace.commands.run import Run
from untaped_workspace.commands.status import List, Path, Status
from untaped_workspace.infrastructure.project_store import CHECKOUTS, HELD_REPOS, META, WORKTREES
from untaped_workspace.settings import WorkspaceSettings


class WorkspacePlugin(Plugin):
    """Create, archive and remove task workspaces (git worktrees of several repos).

    A workspace is a project (`untaped project --help`): a folder whose
    `.untaped/` keeps workspace's rows, one per repo. Each repo in it is a git
    worktree of that repo's copy in the git plugin's repo store
    (`git.store_dir`), on the task branch, or read-only at a base branch or
    tag, and the project holds the repo, so the store keeps it while any
    project uses it. Its life: `create` (the project
    `workspace.workspaces_dir/NAME`) or `add` in any project, work and push,
    `archive` (the worktrees go; the folder, the branches and the holds stay),
    then `remove` (the repos are released, and the project `create` made).

    Every command run inside the project reads its settings
    (`.untaped/config.yml`), the credential helper git runs in a worktree
    included: `untaped --profile staging workspace create NAME` records the
    pick there, so the folder keeps the environment it was made in.

    ## Names

    - A workspace name is one path segment: letters, digits, `.`, `_` and `-`,
      not starting with `.`. Anything else exits 2.
    - Every command that takes NAME takes a workspace's name or a project's
      folder (`./PROJ-1`, `~/work/PROJ-1`), and, except `create` and `remove`,
      the project containing the current directory when NAME is left out;
      outside one, leaving it out exits 2. Two active workspaces with one
      name exit 2 listing both folders; an archived name names the newest.
    - Each repo directory is the repo name. When two repos in one workspace
      share a name, the later ones use `OWNER-NAME`.

    ## Naming repos

    `--repo` and `--read-only` take, and `--stdin` reads:

    - `OWNER/NAME`, or a bare `NAME` that is unique, both looked up in the repos
      every installed repo source lists (the plugins that fill `RepoSource`).
      The lists are cached (`workspace.caches.repo_source_repos.max_age_seconds`).
      When two sources list the same name,
      `untaped plugin rank workspace.repo_source repos PLUGIN...` sets their order.
    - A clone URL: `https://…`, `ssh://…` or `user@host:path`. It skips the
      lookup, and its repo is named by its path (`acme/api`). Local paths and
      `file://` URLs are refused. A URL on a host a plugin claims must name a
      repo that plugin's settings can reach.

    The source decides each repo's clone URL, and the URL is saved with the
    workspace; `untaped workspace repos resolve NAME` asks again after a source's
    setting changes.

    Git never prompts for credentials when untaped runs it. Fetching from a host
    a plugin claims uses that plugin's token. Over https, `git push` in a worktree
    asks `untaped git credential` after your own credential helpers (instead of
    them with `git.untaped_helper_only`). Any other host needs a credential
    helper or an SSH agent.

    ## Branches and bases

    Writable repos share one branch, and every repo, read-only ones included,
    has one base. A base names a branch or a tag on origin; a branch wins when
    both exist, and a base that is neither fails that repo as `not_found`.

    How a writable repo is checked out, with the row's `action` and `detail`
    ("both": a local branch and one on origin):

    | The branch in the repo store | Result |
    |---|---|
    | Exists nowhere | `created`, `from origin/BASE` (`from tag BASE` for a tag) |
    | On origin only | `checked_out`, `tracking origin/BRANCH` |
    | Local only | `checked_out`, `resumed local branch` |
    | Both, local behind or equal | `checked_out`, fast-forwarded, `tracking origin/BRANCH` |
    | Both, local ahead | `checked_out`, `resumed; ahead of origin/BRANCH` |
    | Both, diverged | `checked_out`, `resumed; diverged from origin/BRANCH` |

    The upstream is set to `origin/BRANCH`, so `git push` works. A branch is
    checked out in one worktree at a time: a branch already checked out in
    another workspace is a `failed` row with category `conflict`.

    `--read-only` checks a repo out detached at `origin/BASE`
    (`read-only at origin/BASE`, or `read-only at tag BASE`). A commit made there is on no
    branch, so archiving counts it as unpushed and refuses.

    ## The history backfill

    The repo store keeps repos blobless. After the worktree is in place, `create`
    and `add` fetch the files of every commit of the remote's branches and tags,
    so `git blame`, `git log -p` and old tags work offline. The backfill never
    fails a repo: when it stops, the row is still `created` or `checked_out`, the
    command exits 0, a warning names the repo, and
    `untaped workspace status --fetch NAME` resumes it. Until then git fetches
    missing files on demand.

    ## Archiving

    `archive` checks each repo offline: it sees origin as of the last push or
    fetch. A repo blocks archiving with any of:

    - uncommitted changes;
    - stash entries made on that repo's branch;
    - commits on `HEAD` that no remote branch nor the base tag has, in read-only
      repos too;
    - an initialised submodule;
    - the repo missing from the repo store;
    - unreadable git state (`status` shows `state` `error`).

    While any repo blocks, archive exits 1, removes nothing, and its hint says
    what to do for each blocker. `untaped workspace status NAME --check` reports
    the same blockers.

    `--force` archives a blocked workspace after a confirmation: it discards
    uncommitted work and commits made in a read-only repo, which cannot be
    recovered, and removes the directories. Branch commits and stashes stay in
    the repo store. Without `--force`, each repo is checked again just before it
    is removed, and work made after the first check fails that repo as a
    `conflict`. If removing a repo fails, the workspace stays active so archive
    can run again.

    Archiving removes the worktrees; the folder, its other files and
    `.untaped/` stay, and `untaped workspace list --archived` lists it. The
    project keeps holding its repos, and the branches stay in the repo store
    and on the remote, so `untaped workspace create NAME` (or `add` with the
    project's folder, for a project `create` didn't make) resumes it: each repo
    is checked out again on its branch.

    ## Removing

    `remove NAME` takes every workspace of that name, active and archived, and
    lets go of the repos each project holds; `untaped project remove` lets go
    of them the same way. For each repo:

    - Uncommitted, stashed or unpushed work in its worktree, as archive checks
      it, refuses the removal (exit 1, nothing changed); `--force` discards it.
    - When another project holds the repo, only this project's worktree goes;
      the repo and its branches stay.
    - Otherwise the worktree goes and the repo is released from the repo store:
      workspace's refs, and the local branches that no worktree has checked out
      and whose commits are all on the remote, go; the repo itself goes when no
      other plugin uses it. The workspace's own branch with commits no remote
      branch has refuses the removal ("branch BRANCH: N commits not pushed");
      `--force` deletes it. Other branches with unpushed commits or a stash made
      on them stay and keep the repo (`held by branches` in
      `untaped git store`); `--force` deletes them too, and the preview names
      them. A stash itself is never deleted, even when its branch is.

    Then workspace's rows go, and the project itself when `create` made it and
    it holds nothing else: `config.yml` and your own files stay, and the folder
    goes when nothing else is in it.

    ## Shared by every workspace of a repo

    - Stashes: `git stash list` in one workspace shows the stashes made in the
      others too.
    - The repo store: worktrees point into it, so deleting a store repo by hand
      breaks every workspace using it; `remove` releases what no project holds
      any more. The store locks each repo for every write, so concurrent runs
      wait. `create`, `add`, `archive` and `remove` of one workspace also wait
      for each other (they hold the project's lock from their check to the
      change): an `add` queued behind an `archive` then resumes the workspace.
    - Worktrees are locked (`git worktree lock`), so a moved project, or one on
      a drive that is away, keeps its entries; `untaped project move OLD NEW`
      reconnects them.
    """

    settings = WorkspaceSettings
    stability = experimental
    commands = (Create, Add, List, Status, Path, Archive, Remove, Run, ReposGroup)
    offers = (RepoSource,)
    checkouts = CHECKOUTS
    repos = HELD_REPOS
    worktrees = WORKTREES
    meta = META
    skill = StubSkill(
        "untaped workspaces: one project folder per task holding git worktrees of several "
        "repos on a shared branch. Use when the work concerns an untaped workspace: "
        "making one for a task or ticket, adding repos to one, finding, checking or "
        "cleaning one up. A repo outside a workspace needs only plain git."
    )


__all__ = ["WorkspacePlugin"]
