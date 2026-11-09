# 0029. Ambiguity fails by default; resolvers

- **Status:** amended · 2026-11-05 · the maintainer
- **Rests on:** [0012](0012-two-engines-refuse.md) two engines for one slot: refuse · [0027](0027-kernel-extension-points.md) the kernel's own extension points
- **Amended by:** [0049](0049-kernel-hooks-loaded.md) `Resolver` is classified as a **question hook** (it states how several answers combine and what happens when none comes) · [0039](0039-undecided.md) "no answer" is its own outcome, `Undecided`
- **Reopen if:** true one-of slots turn out common, so declaring preferences becomes a chore.

## Problem

Two engines installed for one slot. The maintainer first proposed a user priority list resolved by the kernel. Objection (theirs): the list comes from configuration, and the kernel has no dependencies and reads no files. A `Preferences(picks, prefer)` input handed to the kernel by the host solved that, but raised "who provides it?". The maintainer's refinement: plugins are the kernel's only job, so **the kernel should fail on ambiguity by default and let a plugin come to the rescue.**

## Decision

1. **Ambiguity fails by default.** The kernel offers `Resolver` as an **extend** contract (a slot would itself need resolving: a loop). Every resolver is asked; an answer is accepted only when all that answer agree and it names an installed engine; otherwise `Ambiguous`, with the answers.
2. **`untaped-plugins`** (placeholder) fills `Resolver` from **what the user declared**, answers nothing when that doesn't decide, never invents a preference, and owns the `untaped plugin …` commands and the slot-state doctor check.
   - ✗ "first-party wins" · ✗ "alphabetical" as a silent default
3. **The kernel drops the `Preferences` input.** An embedded program that wants a choice writes a tiny resolver.

Resolution: one engine → it; none → `Unfilled`; several → ask the resolvers; one agreed answer → it; else refuse.

## Rejected

- **A priority list resolved by the kernel:** the kernel would need configuration.
- **`settings.preferences(plugins)`:** put a plugin-management method on the settings engine, which reads any section by its model and knows no particular one.
- **Engines declaring priorities** (already rejected in 0012): two "preferred" engines is the same problem again. A list the *user* writes is a generalised pick, so "results never depend on install order" still holds.

## Consequences

- **The maintainer's caution: `[plugins] prefer` assumes things.** It was only an illustration. Not decided: the file format (the settings engine is swappable), the section name, one global list vs per slot/profile/project, whether users name plugins or engines, whether any file exists at all (an MCP host or a hub may hold per-user settings elsewhere). The shape is `untaped-plugins`' own design.
- The settings slot can't be resolved by a resolver that reads settings: the loop rule ([0030](0030-loop-and-bootstrap.md)).
- The maintainer: "the kernel runs on a few principle rules that make everything else work after that." The kernel's rules were first listed here.
