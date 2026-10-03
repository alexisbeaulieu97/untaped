---
name: untaped-ansible
description: Maps Ansible role and project dependencies across GitHub repositories through the `untaped ansible` command (what a role depends on, what depends on it, which projects reach a repo, and dependency graphs). Use when the user asks who uses a role, the impact of changing one, or about requirements.yml or meta/main.yml dependencies.
---

# untaped ansible

A dependency answer is only as complete as the data behind it: before
calling a list complete, know which data answered it (live GitHub or a
source's cache), how fresh that cache is, and where the walk stopped.

## Setup

- GitHub access comes from the `github` settings: a token is required.
  Private repos also need Git access over HTTPS with that token, or SSH when
  `ansible.git_clone_protocol` is `ssh`.
- A *source* is a saved set of orgs, teams and repos whose dependency files
  are scanned into a local cache. Sources and source aliases are `ansible`
  state; `ansible.default_source` names the source used when no `--source`
  or inline selector is given.

## Commands

| Question | Command | Use when |
|---|---|---|
| What does ROLE depend on? | `untaped ansible deps acme/base-role` | downstream; works live without a source |
| What depends on ROLE? | `untaped ansible impact acme/base-role` | upstream, blast radius; needs a refreshed source |
| Which roots reach REPO? | `untaped ansible find acme/base-role --root acme/site` | you have the roots (projects, job templates) and want the ones that contain REPO |
| Show both directions | `untaped ansible graph acme/base-role` | a human-readable report; `--format mermaid` only when the user wants a diagram |
| Is the cache fresh? | `untaped ansible source status` | before trusting an `impact` answer |
| Save and index a source | `untaped ansible source set platform --org acme`, `untaped ansible source refresh platform` | first use, or a stale source |

`ROLE` is `owner/repo`, a GitHub URL, a source alias, or a local path. For a
subdirectory of a checkout, such as `./roles/web`, add `--target-repo
OWNER/NAME`.

## Where the answer comes from

| Situation | Downstream (`deps`, `find`, `graph` down) | Upstream (`impact`, `graph` up) |
|---|---|---|
| No source selected | live GitHub reads | unavailable: `impact` fails, `graph` omits it with a warning |
| A source selected (`--source`, inline `--org`/`--team`/`--repo`, or the default) | the source's cache; a role outside it warns "not cached" and gives no rows | the source's cache |
| `--live` with a source | live GitHub reads | the source's cache |
| `--refresh` | refreshes the source first, then reads the cache | the same |

`--refresh`, `--cached` and `--live` are mutually exclusive (exit 2).
Repeated `--source` and inline selectors add up. Repeating the same inline
selectors reuses their cached scan.

## Workflows

### Answer "what depends on X"

1. Run `untaped ansible source status`. Refresh when the source is `stale`
   or `not_refreshed`: `untaped ansible source refresh platform`.
2. Run `untaped ansible impact acme/base-role --format json`. Without
   `--ref` it covers every ref a dependent pins; `root_ref` tells them apart.
3. Check each row's `stopped`. `depth` or `not_cached` means the next level
   was not read, so more dependents may exist beyond that repo.
4. Read the stderr warnings: skipped dependency files, ignored collections,
   omitted unpinned dependents.
5. Report the dependents per ref, and name any stopped repos and warnings as
   gaps rather than dropping them.

### Answer "what does X depend on"

1. Run `untaped ansible deps acme/base-role --format json`, adding `--ref`
   for a branch, tag or SHA; the default branch is read otherwise.
   `--all-refs` reads every cached ref of X instead (needs a source).
2. With a source selected, a "not cached" warning means X is outside it:
   pass `--live`, or widen the source and refresh.
3. Check `stopped` and the warnings as above before reporting.

### Map a repo back to job templates

`find` gives every input record its own rows, so results join back to the
inputs:

```bash
untaped awx job-templates list --with-scm --format pipe \
  | untaped ansible find acme/base-role --stdin --format json
```

A root that never reaches the repo gives no row. With `--depth N`, an empty
result only means "not within N levels".

## Safety

- `source remove` and `source-alias remove` confirm. Preview with
  `--dry-run`, show the user what goes, then pass `--yes` once approved.
  Without a terminal they need `--yes` or `--dry-run` (exit 2 otherwise);
  declining exits 1 with `cancelled; no changes made`.
- Exit codes: 0 success, 1 failure (including a partial refresh), 2 usage
  error, 4 fix the environment (settings, a rejected token, `git` missing),
  5 retry later (timeout, network, rate limit, paused refresh), 130
  interrupted.

## Pitfalls

- Read stderr as well as the rows; under `--format json` it is JSON Lines.
  Pass on to the user, quoted with its hint, what they would act on: a
  deprecated setting or flag, a skipped or partial result, a clamped option.
  Leave out progress and routine info lines.
- `repo@v1` and `repo@main` are different nodes; never merge them in a
  report. An unpinned dependency points at the default-branch node.
- A partial refresh exits 1 (or 5 when a failure was transient) with
  `refresh completed with N repo failures; successes were saved`. That is a
  partial success: rerun the same command to retry only the failures.
- A refresh that runs low on GitHub API budget stops, exits 5 and resumes
  where it left off when rerun.
- Collections in requirements files are not followed; a warning lists them.
- Cycles are found only within the depth that was walked.
- `source set` replaces a source; use `source patch` to add or remove one
  org, team or repo.
- Source aliases apply at refresh: run `source refresh` after changing one.

## References

| File | Read it when |
|---|---|
| [references/graphs.md](references/graphs.md) | explaining a surprising result: ref and repo resolution, unpinned dependencies, local targets, `stopped`, tree or JSON graph output, skipped files |
| [references/sources.md](references/sources.md) | managing sources and aliases, choosing a refresh backend, handling a failed, partial or paused refresh, or a rebuilt cache |
