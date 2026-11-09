# 0002. Words, engines, users

- **Status:** accepted · settled before 2026-11-05 (foundation point 1; the maintainer: "Perfect.") · the maintainer
- **Rests on:** [0001](0001-make-your-own.md), the "make your own" test
- **Reopen if:** splitting words from engines costs more than the swaps it enables, or owners can't keep their words small and slow. The rejected option wins if nobody ever actually replaces a first-party engine.

## Problem

Today git's `api` mixes three things:
- words: `Repo`, `RepoSelector`, `GitUrl`;
- extension points: `RepoSource`, `GitHost`;
- the engine: `RepoStore`, `run_git`, `clone`, `ls_remote`.

So ansible, workspace and dotfiles can't use repos without importing the maintainer's git engine, and "make your own git" is impossible.

## Decision

Users depend on **words**, never on an engine. Depending on a specific engine is allowed, but it is visible and gives up the swap.

- **Words:** meaning only.
- **Engine:** code that fills words.
- **User:** anything that depends on words.

These are roles, not kinds of package: one plugin can be an engine for some words and a user of others.

```python
# ✗ ansible imports the maintainer's engine to use a repo
from untaped_git.store import RepoStore, run_git

# ✓ ansible depends on git's words; any engine can fill them
from untaped_git import Repo, repo_store
```

Splitting them is good design even if nobody ever writes another git.

## Rejected

- **"Don't let people pick their git."** It's simpler, but "everything is a plugin" would then really mean "everything is a package".

## Consequences

Every later bucket and packaging rule builds on this split: words in their own packages ([0021](0021-package-line-rule.md)), engines filling them, and users never reaching into engines ([0025](0025-parties-bucket.md), X1).
