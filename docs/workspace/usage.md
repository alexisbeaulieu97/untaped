# Workspaces

`workspace` is [experimental](../stability.md#experimental) and may change in
a minor release.

A *workspace* is one directory per task. It holds a git worktree for each
repo you need, all on the same branch, so you (or an agent) can change several
repos for one ticket and archive the lot when the work is pushed. Worktrees
share a bare cache of each repo, so creating a workspace is fast and cheap,
and parallel tasks get isolated checkouts of the same repos.

Commands never discard local work: archiving refuses while a repo has
uncommitted, stashed or unpushed work. The
[packaged skill](../../src/untaped/capabilities/workspace/skills/untaped-workspace/SKILL.md)
and its references hold the per-command detail; `--help` and `--columns ?`
hold the options and fields.

## Create, work, archive

```bash
untaped workspace create PROJ-123 --repo acme/api --repo acme/web
cd "$(untaped workspace path PROJ-123)"
# commit and git push in each repo, then:
untaped workspace status PROJ-123 --check
untaped workspace archive PROJ-123
```

`create` prints one row per repo: a new branch from the base, or an existing
branch resumed. `status --check` exits `3` while anything would block
archiving; `archive` removes the worktrees and keeps a record. The branches
stay in the repo cache and on the remote, so creating a workspace on the same
branch later resumes the work.

## Add repos later

```bash
untaped workspace add PROJ-123 --repo acme/infra
```

`add` also reads repos from a pipe, for example a GitHub inventory:

```bash
untaped github repos list --team acme/platform --format pipe | untaped workspace add PROJ-123 --stdin
```

## Read-only repos

`--read-only` checks a repo out at its base branch, detached, for reference
code you will not change. Archiving still refuses while it has local changes
or commits of its own.

## Jump in

```bash
cd "$(untaped workspace path PROJ-123)"
```

`untaped workspace list` shows active workspaces (`--archived` the rest).
Inside a workspace directory, the name may be left out.

## Run a command in every repo

```bash
untaped workspace run PROJ-123 'git push -u origin HEAD'
```

`run` also takes a script file or a script on stdin, runs in the writable
repos, and exits 1 if any repo failed. Forms, environment variables,
selection and timeouts are in the
[run reference](../../src/untaped/capabilities/workspace/skills/untaped-workspace/references/run.md).

## Settings

`workspace.cache_dir`, `workspaces_dir`, `parallel`, `branch_template` and
`protocol` are in the [configuration reference](../reference/config.md#workspace).
Do not delete the cache directory while workspaces are active: the worktrees
point into it. A repo name that is not found is looked up in the GitHub
inventory, set by `github.inventory` orgs and teams.

## Output

Every command prints rows you can reshape with `--format` and `--columns`;
see [Pipes and record kinds](../reference/pipes.md#workspace) and
[Exit codes](../reference/exit-codes.md).

## See also

- [Configuration](../configuration.md) and the
  [configuration reference](../reference/config.md#workspace).
- [GitHub](../github/usage.md), to find repos to add, and
  [Recipes](../recipe/usage.md), to change files across a workspace.
