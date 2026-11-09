# 0048. The kernel requires only what it needs

- **Status:** accepted · 2026-11-06 · the maintainer
- **Rests on:** [0042](0042-role-vs-bucket.md) what a plugin declares; [0047](0047-architecture-vs-design.md) architecture vs design
- **Amends:** [0042](0042-role-vs-bucket.md): `uses` leaves the kernel.

## Problem

[0042](0042-role-vs-bucket.md) made every plugin declare `offers`, `fills` and `uses`. Checking each kernel piece against the entry test raised: which of these does the kernel actually need?

## Decision

The maintainer: the kernel requires only what it needs for everything after it to work. If a plugin needs something more, that plugin is its owner, and the kernel never forces it on everyone.

- `offers` and `fills` are **mandatory**: the kernel routes with them.
- `uses` is **optional**: it belongs to whichever plugin wants it (e.g. a doctor's words).

```python
class Jira(Plugin):
    offers = (JiraSource,)
    fills  = (List, Show)
    uses   = (settings_store,)       # ✗ required by the kernel: it never reads it to route

class Jira(Plugin):
    offers = (JiraSource,)
    fills  = (List, Show)            # ✓ all the kernel needs
```

Without `uses`, the same fact appears when the call is made (`Unfilled`). Only earliness is lost, and a plugin can win it back.

## Consequences

- The Service Locator caution (`ask(...)` hides dependencies) is now answered by `Unfilled` at the call plus an optional `uses` owner.
- The same reasoning later answered concern 1 of the stress test ([0054](0054-recording-is-atomic.md)): catching a bad filler early is an owner's choice, like `uses`.
- Kernel pieces checked against the entry test so far: find and load, `LoadProblem`, `offers`/`fills`, `Contract`, `Slot`, `Resolver`, the outcomes pass; `uses` out. Next were `OwnerCheck`, `Dispatch`, `scope`.
