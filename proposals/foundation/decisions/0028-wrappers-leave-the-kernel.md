# 0028. Wrappers leave the kernel; a `Dispatch` slot stays

- **Status:** amended · 2026-11-05 · the maintainer
- **Rests on:** [0027](0027-kernel-extension-points.md) the kernel's own extension points
- **Amended by:** [0050](0050-dispatch-question-hook.md) `Dispatch` is a **question hook**: exactly one carrier per call, chosen like any slot (a scope binding first); how calls are carried, wrapper order included, is the design of whoever fills it

## Problem

The maintainer asked what wrappers are, whether they are necessary, and whether they should be a plugin if they cause the kernel problems. A wrapper is middleware around every contract call, written once and applied to all calls (cache, retry, timing, test recording). Owning a wrapper chain put recursion (calls to the kernel's own points) and wrapper order into the kernel.

## Decision

- G2 and E6 need the **capability** "engines act on calls from outside"; they don't require the kernel to own a chain.
- The kernel keeps **one point where a call happens**, offered as its own slot: `Dispatch`. When nothing fills it, the kernel calls the provider directly.
- A plugin (placeholder `untaped-calls`) fills `Dispatch`, offers `CallWrapper`, and owns wrapper order and the recursion rule.
- A hub can fill `Dispatch` differently, forwarding calls to runners.
- Pattern named here: **when one of the kernel's own extension points is empty, it does the minimal thing** (call directly; refuse an ambiguity).

## Consequences

- Questions (c) recursion and (d) wrapper order leave the kernel.
- "Cache outside retry" may be meaning rather than a user's taste (closer to pluggy's `tryfirst`/`trylast`); that question later became part of finding 4 ([0045](0045-two-kinds-of-order.md)) and then the design of `untaped-calls`.
- Review finding 5 (a hub needs wrappers and forwarding together, but `Dispatch` is one-of) was answered by 0050: it is the design of whoever fills `Dispatch`.
