# 0050. `Dispatch` is a question hook

- **Status:** accepted · 2026-11-06 · the maintainer
- **Rests on:** [0028](0028-wrappers-leave-the-kernel.md) wrappers leave the kernel; [0049](0049-kernel-hooks-loaded.md) kernel hooks
- **Amends:** [0028](0028-wrappers-leave-the-kernel.md): `Dispatch` is a question hook, not an ordinary slot.

## Problem

`Dispatch` was the kernel's one place where a call happens ([0028](0028-wrappers-leave-the-kernel.md)). Under the hook test ([0049](0049-kernel-hooks-loaded.md)), does it belong in the kernel? Review finding 5 had also found a hub needs wrappers and forwarding together, while `Dispatch` is one-of.

## Decision

Compared side by side (the maintainer: "When we see them side by side like this, the answer is much more obvious, B."):

| | A. Carrying inside the kernel | **B. A hook** | C. No hook |
|---|---|---|---|
| Who carries calls | the kernel | whoever fills `Dispatch` | each plugin, its own way |
| Problem | wrapper order, parallelism, forwarding and the network go into the one unswappable thing | — | calls through plain `ask` slip past: no plugin can be sure to see every call; host-bound proxies miss extend contracts |

**B.** `Dispatch` is a question hook, "how is this call carried?":

- exactly one carrier per call, chosen like any slot: a scope binding first (so a hub binds a forwarding dispatcher per job), then resolvers, then `Undecided`/`Ambiguous`;
- empty → the kernel calls the provider directly.

The kernel calls its own hooks directly; they are never dispatched (that would loop through resolving `Dispatch`).

## Consequences

- **Finding 5 (wrappers vs forwarding) leaves the architecture.** How calls are carried (cache, retry, timing, recording, forwarding, their order) is the design of whoever fills `Dispatch`; in ours, `untaped-calls`.
- The kernel's remaining opinion here is the shape of what it hands the dispatcher and gets back: settled in [0051](0051-reply-and-result.md) and [0052](0052-call-shape.md).
- Parked (design, not kernel): can a caller choose how its call is carried, or require "local"?
