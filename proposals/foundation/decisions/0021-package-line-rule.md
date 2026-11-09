# 0021. Package lines and identities

- **Status:** accepted · 2026-11-05 (afternoon) · the maintainer
- **Rests on:** [0002](0002-words-engines-users.md) words, engines, users · [0013](0013-versioning-per-package.md) versioning per package · [0020](0020-core-is-the-kernel-only.md) core is the kernel only
- **Reopen if:** pip and pipx stop being supported builders; release automation can't become per-package; the line rule stops giving repeatable answers.

## Problem

Where should one package end and the next begin, for every language, not only untaped's? A package fixes five things together: what you install, what you version, what others depend on, what installing it brings (commands, libraries, load time), and its identity (the entry-point name owns the settings section and the namespace users write). The first candidate, "cut wherever someone could reasonably want one side without the other", always said *cut*: under placement by intent ([0011](0011-placement-by-intent.md)), a swap is always reasonable.

## Decision

1. **Package-line rule:** cut where words can be used apart, unless they change for the same reason. This is Robert C. Martin's Common Reuse Principle (don't make people depend on what they don't use) weighed against the Common Closure Principle (what changes for the same reason is released together). Reason by intent, never by today's usage.
2. **A package that offers a replace slot never ships its filler.** It follows from the rule: an engine changes for different reasons than its words.
   - ✗ `untaped-git` offers `repo_store` and fills it.
   - ✓ `untaped-git` offers `repo_store`; `untaped-git-cli` fills it.
3. **Identity = distribution = top-level import package**, one to one, named `untaped-<identity>`. An agent can go from a plugin list to an uninstall command.
4. **Grammar arrows across a cut point one way.**
5. **Identity names are role-free.** The words own the bare namespace name (`git`). Roles change over time and a package can hold several, while identities appear in users' configuration.
6. A plugin that later adds a slot gets a **new package with its own import package**. Adopting it is opt-in and nothing breaks.

The five families became separate packages under this rule (later four, see [0038](0038-settings-and-no-run-family.md)); inside one family a word and its marks change together and stay together.

## Rejected

- **One distribution per plugin with per-role entry points, split later (prototype B).** Verified unsafe: upgrading `untaped-git` 1.0 → 2.0 with pip installs the new words distribution, then uninstalls 1.0, whose file list names the shared `untaped_git/words.py`, so it is deleted (`ModuleNotFoundError`). Reviewers also found: rollbacks and mixed installs register two `git` identities with `pip check` silent; uninstalling either deletes the other's files; CI never sees it (fresh, editable installs). It also ties the words' version to the engine's and keeps the default engine installed for everyone who replaces it.
- **"Extend-only plugins stay one package"** (the converged rule's point 3): replaced by the line rule.
- **Cutting only where a swap mechanism needs it:** the line rule covers that case and also sees rival vocabularies, which a slot-only rule missed.

## Consequences

- Roughly 16–18 packages rather than 11 or ~30 (estimate from the panel).
- First-party floors become real promises: CI tests the lowest declared and newest versions per package (`--no-sources` jobs), and releases become per-package.

## How we got here

Three reviewers (third-party author, maintainer/release, user/operator), briefed neutrally on two prototypes, all chose role-split packages and converged on a mechanical rule. Lessons kept: prototype the claim, and test changes (upgrade, rollback, mixed versions), not just fresh states; today's usage counts are examples, never evidence of demand.
