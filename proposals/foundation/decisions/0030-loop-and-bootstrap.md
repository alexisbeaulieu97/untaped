# 0030. The loop rule and the bootstrap closure

- **Status:** accepted · 2026-11-05 (decision 5; finding 1 in the evening) · the maintainer
- **Rests on:** [0029](0029-resolvers.md) resolvers
- **Settles:** [0016](0016-engines-depend-on-words.md)'s parked "cycles refused" (A needs B, B needs A)

## Problem

Resolving the settings slot would ask a resolver that needs settings: a loop. Engines that need each other's slots loop the same way. A first version made the outer result `Ambiguous` with the loop attached; the design review (finding 1) showed that this loses the host's bootstrap choice, and that an unstated "bootstrap closure" existed.

## Decision

- **The kernel never re-enters a slot it is resolving.** The inner call gets `Loop(chain=…)` as data.
- **A resolver that hits `Loop` gives no answer; only that resolver drops out.** The others still count. (The maintainer: "ambiguous is when you get more than one answer.")
- **Bootstrap closure:** the slots that resolvers reading settings can't decide (the settings engine, and what it and the resolvers need) are decided by being the only engine installed, or by resolvers that need nothing from settings, such as the host's own environment resolver. The closure is stated, and doctor reports it.
- The CLI host's `EnvResolver` reads its process environment (through the `process` slot, [0041](0041-process-slot-and-host.md)); an MCP host or a hub resolves its own way.

## Rejected

- **Outer result `Ambiguous` with the loop attached:** it threw away a valid answer from a resolver that needed nothing from settings.
- **"Bootstrap happens at env":** the maintainer corrected this; reading the environment is the env resolver's job, not a phase.

## Consequences

- [0012](0012-two-engines-refuse.md)'s "pick from the environment" stays, but the host reads it, not the kernel.
- "Several engines and no answer" is not "more than one answer", so it needed its own outcome: [0039](0039-undecided.md).
