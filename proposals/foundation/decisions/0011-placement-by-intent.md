# 0011. Placement check, by intent

- **Status:** amended · settled before 2026-11-05 (foundation point 10; the maintainer: "Perfect.") · the maintainer
- **Rests on:** [0001](0001-make-your-own.md), deciding by intent
- **Amended by [0020](0020-core-is-the-kernel-only.md):** "core, if core reads it" now means the kernel, and the clause "or two strangers need it to meet and nobody else naturally owns it" no longer applies: the kernel holds no vocabulary, so such meaning goes to words.
- **Reopen if:** `@experimental` doesn't really let shapes change cheaply until they're proven. The rejected option (demand-based placement) would win if publishing early produces many unused public names despite `@experimental` (#832 found 81 of 245 SDK names unused).

## Problem

Where does a new thing go: private to its plugin, a published capability, core, someone's words, a plain library?

## Decision

1. **Capability or detail?** Could a different implementation reasonably exist, or could someone build on it?
   - Detail → private to its plugin.
   - Capability → continue, and publish it `@experimental` until its shape is proven. This matches issue #832's approved rule: "every name is imported by a first-party plugin, or is experimental with its first consumer written down".
2. **Meaning or doing?**
   - Meaning → core, if core reads it, or if two strangers need it to meet and nobody else naturally owns it; else an existing owner's words; else a new words package.
   - Doing → a plugin, if it fills words, must be configurable or swappable, or touches the run; else a plain library.
3. **Ownership comes before packaging.**

Misclassifying a capability as a detail is the costlier mistake in a decentralized ecosystem: the second user is someone you never see. `@experimental` caps the cost of the opposite mistake.

## Rejected

- **Starting with "does anyone besides its author need it yet?"** The maintainer rejected it as demand-based rather than intent-based: "A 'doesn't need it yet → private' classification could become a refactoring effort a week from now. We're already in a decentralized plugin architecture mindset."

## Consequences

- **Being widely used doesn't put something in core.** Most plugins will use the shared words, yet they aren't core, because core doesn't read them.
- AGENTS.md's "core holds only what every plugin needs" must be updated to this check (assessment input).
