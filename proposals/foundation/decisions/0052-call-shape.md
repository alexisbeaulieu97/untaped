# 0052. The `Call` shape (F3) and `PluginId`

- **Status:** amended · agreed 2026-11-06, recorded 2026-11-07 · the maintainer
- **Rests on:** [0050](0050-dispatch-question-hook.md) `Dispatch` is a question hook; [0051](0051-reply-and-result.md) `Reply` and `Result`
- **Amended by:** [0056](0056-multi-host-parked.md): the multi-host guardrail is parked and no longer in force. F3 and `PluginId` stay as decided here.

## Problem

The kernel's one remaining opinion about carrying calls is what it hands the dispatcher. That shape can't be swapped, so it must not decide anything a dispatcher should decide (order, parallelism, forwarding).

## Decision

**F3.** The kernel hands the dispatcher the bare call and offers its two steps as separate tools:

```python
Call(point, method, args, kwargs)              # the package
find(call) -> providers                        # the address book
invoke(call, provider) -> Result               # knocking on one door
```

- A dispatcher returns **one `Reply` per provider found**.
- Empty `Dispatch` → the kernel composes them minimally, one after another:

  ```python
  [Reply(p, invoke(call, p)) for p in find(call)]
  ```
- Slot calls: `Ok` → the value; `Err` → raised to the asker.

Metaphor used: the kernel is the post office's address book plus the knock on the door; the dispatcher is the courier, who chooses the route, the order and whether to send several vans at once.

**`PluginId`.** `Reply.provider` is a `PluginId` (the distribution name), not a class.

**Multi-host guardrail:** every kernel shape that crosses `Dispatch` is plain data, rebuildable on a machine that has the contract's words but not necessarily its providers. A distribution name is a string at the packaging and wire boundary, which typed references allow.

## Rejected

- **F1, find first, then hand over the found providers:** fails on a hub, where the provider isn't installed on the machine doing the finding.
- **F2, `proceed`** (the dispatcher calls one kernel function that finds and invokes): bundles the two steps and takes parallelism away from dispatchers. The maintainer: "Isn't that opinionated?"

## Consequences

- Multi-host beyond the guardrail is parked; single-host is settled first (the maintainer).
- These shapes are part of the founding principle's protocol: the package (`Call`) and the receipt (`Reply`) ([0053](0053-founding-principle.md)).
