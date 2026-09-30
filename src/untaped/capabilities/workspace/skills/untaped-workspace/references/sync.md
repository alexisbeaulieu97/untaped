# sync, status and branch apply

## sync

`sync` clones repos that are declared but missing, then, for each existing
clone, fetches `origin` and fast-forwards it to its branch's upstream when
that is safe. Every repo gets one row; its `action`:

| Action | Meaning | What to do |
|---|---|---|
| `cloned` | was missing, now cloned | nothing |
| `pulled` | fast-forwarded (`detail` counts the commits) | nothing |
| `unchanged` | already up to date, or only ahead | push if ahead |
| `skipped` | left alone on purpose; `detail` says why | see below |
| `failed` | a git step errored; `detail` names the step | read `error`, then fix or retry |
| `removed` / `planned` | a prune deleted or would delete an orphan | see prune.md |
| `unmatched` | `--all --repo R`: this workspace does not declare `R` | check for a typo |
| `unavailable` | `--all`: this workspace's manifest could not be read | fix or `forget` the workspace |

Common `skipped` details:

- `dirty working tree`: local changes; ask the user to commit or stash.
- `on <branch>, expected <target>`: the clone is on another branch; `sync`
  never checks out. Use `branch apply` if the user wants the target.
- `diverged from origin`: local and remote both have commits; rebase or merge
  by hand.
- `no upstream`: the branch tracks nothing, so there is nothing to pull.
- `not a git repository`: a declared directory without its own `.git`; git
  never runs there.

`skipped` rows alone exit 0; any `failed` row makes the run exit non-zero
after every row is printed.

### Scope, speed and timeouts

- `--repo R` (repeatable, name or URL) narrows the run. For one workspace an
  unknown `R` aborts; under `--all` it yields `unmatched` rows and the run
  continues.
- Under `--all`, an unreadable manifest is one `unavailable` row with an
  empty `repo`, and the registry is left as it is. A malformed registry aborts
  before anything runs.
- `-j N` sets how many repos run at once (`-j 1` is serial); the limit is
  shared across all selected workspaces.
- `--timeout N` caps every git call (defaults: 60 s for local calls, 600 s for
  clone and fetch). A timeout exits 5, meaning retry later.
- A failed or timed-out clone removes its partial directory, so the next
  `sync` retries it.

### Credentials

Git runs with no terminal prompts and ssh in batch mode, so a remote that
needs a password fails that repo instead of hanging. Use an SSH agent or a
credential helper. If the user configured `GIT_SSH_COMMAND`, `GIT_SSH` or
`core.sshCommand`, untaped leaves it alone; add `-o BatchMode=yes` there to
keep the same behaviour.

## status

- Reports branch, upstream, ahead/behind and modified/untracked counts per
  repo. It never fetches.
- `--dirty` and `--behind` keep only matching repos (either, when both are
  given). They never hide uncloned or failed rows.
- `--check` exits 3 when any repo matches; when a repo could not be inspected
  it exits with that failure's code instead (1, or 5 for a timeout).
- A declared directory without its own `.git` shows `cloned=false` with
  `not a git repository`; a failed `git status` keeps `cloned=true` with the
  error in `detail`.

## branch set, unset and apply

- `branch set WS BRANCH` and `branch unset WS` edit `defaults.branch`, or with
  `--repo REPO` that repo's override. They never run `git checkout`.
- `branch apply WS` (or `branch set ... --apply`) fetches, then checks out
  each existing clone to its target: the local branch, or a new tracking
  branch from `origin/<branch>`.
- Its rows are `checked_out`, `unchanged`, `skipped` or `failed`. It skips
  missing clones, repos without a target branch, and dirty or diverged repos.
- A target that exists neither locally nor on `origin` is skipped with
  `branch not found locally or on origin`; `--create` creates it from the
  current clean HEAD instead.
