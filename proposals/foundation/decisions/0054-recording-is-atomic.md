# 0054. Recording is atomic

- **Status:** accepted · 2026-11-07 · the maintainer
- **Rests on:** [0049](0049-kernel-hooks-loaded.md) kernel hooks, `Loaded`; [0053](0053-founding-principle.md) the founding principle

## Problem

Concern 1 of the stress test: the founding principle says the kernel's records are never half-changed. What does that require when a plugin loads?

## Decision

**The kernel records a plugin's declarations (its addresses) in one step after its import succeeds, or not at all. Nothing registers by side effect. It never claims a plugin works:** a filler its owner dislikes is still routed, and its call ends in `Ok` or `Err`. Catching it earlier is the owner's design (on `Loaded`), like `uses` ([0048](0048-kernel-requirement-rule.md)).

Metaphor: the post office adds Acme's whole address-book page at once, or not at all. If one of Acme's parcels is badly packed, it comes back "returned to sender" (`Err`), which is still a known outcome. The post office never promised Acme packs well; whether customs (the owner) inspects early is customs' business.

How much a mistake costs is the owner's choice:

```python
# owner raises at class creation → the import fails → the whole plugin is LoadProblem
# owner checks on Loaded         → keeps its own verdict; the rest of the plugin is served
```

## Rejected

- **"A plugin loads fully or not at all"** (the first wording): promises more than the principle. It sounds like "the plugin works", and the kernel can't know that; it knows no words.
- **"Everyone reaches an owner's point through the owner's interface"** (proposed by me, withdrawn): it is design, not kernel, and it clashes with calls routed by the kernel.
- **A veto hook** ("admit this declaration?"): fails the hook test's condition 3 ([0049](0049-kernel-hooks-loaded.md)).

## How we got here

I tested the first wording with a plugin holding a good store and a bad command (`output: Output[AcmeClient]`, where `AcmeClient` isn't a `Record`). The command grammar notices on `Loaded`, after the kernel has recorded the plugin, so the kernel said "loaded" while the owner said "partly broken". It looked like an architecture flaw. The maintainer asked whether an earlier decision caused it, then asked for a second look.

Second look: no kernel piece was the cause. Two mistakes in how the test was run:

1. the concern was worded about plugins instead of the kernel's own records;
2. edge 1 used design pieces (`Command`, `Output`, `Record`) that aren't settled, breaking the one-piece-at-a-time rule. That is why a kernel test turned into "what's `Command` again?".
