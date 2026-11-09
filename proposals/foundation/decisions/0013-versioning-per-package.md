# 0013. Versioning: the package, floors only

- **Status:** amended · settled before 2026-11-05 (7.2) · the maintainer
- **Rests on:** [0002](0002-words-engines-users.md) words, engines, users
- **Amended by [0035](0035-pydantic-is-the-notation.md):** an accepted exception. A major of the language's notation (pydantic 3) is a major of every words and grammar package written in it, at once (one environment holds one pydantic).
- **Amended by [0048](0048-kernel-requirement-rule.md):** "a break is caught at load by the `uses-missing` quarantine" no longer holds; `uses` left the kernel, so a missing filler shows at the call (`Unfilled`), or earlier if a plugin owns a `uses` check.
- **Reopen if:** first-party floors can't be kept correct (CI tests the lowest declared and the newest versions). The rejected option (lockstep) would win if keeping floors right across first-party packages proves too costly.

## Problem

The original pasted document versioned contracts explicitly (`V1`/`V2` side by side). Today first-party plugins release in lockstep, and core carries extras that pin every first-party plugin `==11.1.0`.

## Decision

- **The unit is the package (semver), not the contract.** Side-by-side `V1`/`V2` contracts don't fit one Python environment, which holds one version of each package.
- **A transition** is a new name beside the old one plus `@deprecated(replacement=)`. Deprecated names stay two majors. `@experimental` names may change in the owner's minor release.
- **Every package versions independently:** words, engines and core alike. Lockstep is dropped. Words change rarely, so a user can depend on `untaped-git>=2` for years while the engine ships many releases. The maintainer: "it resolves the tension between contracts and implementation versioning".
- **Floors only, no caps.** Older providers are tolerated.
- A core major is everyone's major, so core's words stay few and slow.

## Rejected

- **Explicit contract versions** (the original document's `V1`/`V2`).
- **Lockstep.**

## Consequences

- Floors between first-party packages become real promises, and CI's "lowest declared and newest" test applies to first-party packages too.
- Release facts: `scripts/release.py` and `release.yml` are lockstep today, and `uv sync --resolution lowest-direct` keeps workspace siblings at their source, so first-party floors need per-package `--no-sources` jobs.
