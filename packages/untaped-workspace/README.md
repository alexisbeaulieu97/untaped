# untaped-workspace

Install it as part of `untaped`: `uv tool install 'untaped[workspace]'` or `pip install 'untaped[workspace]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`workspace` is [experimental](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/versioning.md#experimental) and may change in
a minor release.

A *workspace* is one directory per task. It holds a git worktree for each
repo you need, all on the same branch, so you (or an agent) can change several
repos for one ticket and archive the lot when the work is pushed. Worktrees
of a repo share one blobless repo in the git plugin's repo store
(`git.store_dir`), so creating a workspace is fast and cheap, parallel tasks
get isolated checkouts of the same repos, and the whole history (`git blame`,
`git log -p`) is there offline once the backfill after `create` is done.
Archiving refuses while a repo has uncommitted, stashed or unpushed work.

## Set up

Repos named `OWNER/NAME` or `NAME` are looked up in the repos the installed
repo providers list. For GitHub's, set a GitHub token (see
[GitHub](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-github/README.md#set-up))
and the orgs or teams to list:

```bash
untaped auth set github
untaped config set github.inventory.orgs '["acme"]'
```

Fetching a private repo uses the token of the plugin that claims its host
(GitHub's for `github.com`), else your own Git credentials; see the
skill's [setup](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-workspace/src/untaped_workspace/skills/untaped-workspace/SKILL.md#setup).
Clone URLs (`https://`, `ssh://`, `user@host:path`) skip the lookup. Directories, branch
naming and parallelism are in the
[configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#workspace).

## Create, work, archive

```bash
untaped workspace create PROJ-123 --repo acme/api --repo acme/web --read-only acme/docs
cd "$(untaped workspace path PROJ-123)"
# commit and git push in each repo, then:
untaped workspace status PROJ-123 --check
untaped workspace archive PROJ-123
untaped workspace remove PROJ-123
```

Each repo gets a new branch from its base, or resumes an existing one;
`--read-only` repos are reference checkouts. `status --check` exits `3` while
anything would block archiving. `archive` removes the worktrees; the branches
stay, so a later workspace on the same branch resumes the work. `remove`
gives the space back: it drops the workspace's records and, for each repo no
other workspace uses, deletes workspace's refs and the pushed branches from
the repo store, and the repo itself when nothing else uses it. It refuses
while a branch has commits the remote lacks, and keeps a branch with a stash.

## Add repos

```bash
untaped workspace add PROJ-123 --repo acme/infra
untaped github repos list --team acme/platform --format pipe | untaped workspace add PROJ-123 --stdin
untaped workspace add PROJ-123
```

`add` takes repos by name, from a pipe, or, with no repos in a terminal, from
a picker over the providers' repos, the repos in the repo store and any git
URL you paste.
`create` opens the same picker, which also creates the workspace with no
repos selected. `create NAME --empty` makes an empty workspace to `add` to
later.

## Find a workspace

```bash
untaped workspace list
untaped workspace list --archived
cd "$(untaped workspace path PROJ-123)"
```

Inside a workspace directory, the name may be left out.

## Run a command in every repo

```bash
untaped workspace status PROJ-123
untaped workspace run PROJ-123 'git push -u origin HEAD'
untaped workspace run PROJ-123 ./bump.sh
```

`run` has no preview, so check the selection with `status` first. It runs in
every writable repo and exits 1 if any repo failed.

## Reference

The [packaged skill](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-workspace/src/untaped_workspace/skills/untaped-workspace/SKILL.md) is the full reference: every workflow, safety rule and pitfall. Install it for your agent with `untaped skills install workspace --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md#install-skills)); `untaped workspace COMMAND --help` lists each command's options.

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/records.md#workspace) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/exit-codes.md)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#workspace)
- [GitHub](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-github/README.md), which lists repos for workspace (its `RepoSource` provider)
