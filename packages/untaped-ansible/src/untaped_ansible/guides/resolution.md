---
description: "Read when a deps, impact, find or graph result surprises you: which refs are read, how a target names a repo, where a walk stops, and which dependency files are skipped."
---

## Refs

- `repo@v1` and `repo@main` are different nodes.
- Without `--ref`, `deps`, `find` and `graph` read what the target depends
  on at its default branch; `impact` covers every ref a dependent pins.
- `--all-refs` reads every cached ref of the target instead. Local
  checkouts, and `find` roots given with a ref, are read as they are.
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

- A target is `owner/repo`, a GitHub URL, a source alias, or a local path.
  Repo ids match in any case (`Acme/Base` is `acme/base`).
- URLs on the host of `github.base_url` resolve as `github.com` URLs do.
  Path-like sources in requirements files, such as `./local`, stay
  unresolved.
- A local path takes its repo from the Git remote only at the top level of a
  checkout, linked worktrees included; for a subdirectory, pass
  `--target-repo OWNER/NAME`.
- Without a source, a local target is read live. With no GitHub token it
  stays offline and warns that its transitive dependencies were not read.
- Requirements files name some roles by a Galaxy name;
  `untaped agents ansible source-alias set` maps one to its repo.

## Where a walk stops

- `stopped: depth` means `--depth` ran out before the repo's own edges were
  read; one `hint:` on stderr says how many (per root for `find`).
- `stopped: not_cached` means the repo's ref is not in the source's cache.
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
