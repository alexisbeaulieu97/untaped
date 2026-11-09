# 0027. The kernel's own extension points are ordinary ones

- **Status:** amended · 2026-11-05 · the maintainer
- **Rests on:** [0026](0026-kernel-stripped.md) the kernel stripped
- **Amended by:** [0049](0049-kernel-hooks-loaded.md) `OwnerCheck` is gone; the kernel's own extension points are **hooks** of two kinds, moments (`Loaded`) and questions (`Resolver`, `Dispatch`), each earning its place by a stated test

## Problem

The stripped kernel already had two extension points written as special functions: owner checks (K2, `owner_check(Command, check_command)`) and call wrappers (K4). The maintainer's refinement on resolution (fail on ambiguity, let a plugin come to the rescue) needed a third. Special functions are not swappable and invisible in `plugin list`.

## Decision

The kernel's own extension points are **ordinary contracts and slots it offers with its own mechanism** (then: `OwnerCheck`, `CallWrapper`, `Resolver`). The kernel extends itself the way every plugin is extended. The maintainer: "like the DeepSeek harness, where even parts of the kernel are replaceable, here even for its own sake."

```python
# ✗ a registration function only the kernel knows
owner_check(Command, check_command)

# ✓ a declared filler of a point the kernel offers (2026-11-05 shape)
class CheckCommands(OwnerCheck): ...
```

## Consequences

- Grammar owners and wrapping engines declare fillers instead of calling registration functions, and they show in `plugin list`. Plugins that don't hook into the kernel see no change.
- Questions it raised: (a) an explicit composition phase (load all → collect checkers → run checks → compose); (b) a quarantined checker: are its owner's declarations unchecked or refused? (c) calls to the kernel's own points must skip wrappers (recursion); (d) the order of several wrappers. (c) and (d) left with the wrappers ([0028](0028-wrappers-leave-the-kernel.md)); (a) and (b) dissolved when `Loaded` replaced `OwnerCheck` (0049).
- Review finding S2 (later): anyone could fill `OwnerCheck` and veto anyone's words. Answered by 0049.
