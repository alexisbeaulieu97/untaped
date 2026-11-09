# 0033. The mechanics convention

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0029](0029-resolvers.md) resolvers · [0032](0032-picks-and-order-are-resolvers.md) picks and order belong to resolvers
- **Note:** the Service Locator answer below changed with [0048](0048-kernel-requirement-rule.md): `uses` left the kernel, so a missing filler is reported at the call (`Unfilled`), and a plugin may own a `uses` declaration to check it earlier (a doctor).

## Problem

The maintainer's caution: don't make resolvers for everything. One resolver (or wrapper, or renderer) per domain case risks a package explosion, and plugins would lose their value: "one package with extra steps".

## Decision

There are two kinds of extension point:
- **domain** points (`RepoSource`, `repo_store`), filled case by case by nature (github answers `RepoSource`);
- **cross-cutting** points (`Resolver`, `CallWrapper`, renderers, settings sources), filled only by **mechanics** that apply to everything the point touches (`UserOrder`, `Cache`, `Table`).

A cross-cutting filler that names one particular slot, contract or record is a smell.

```python
# ✗ cross-cutting fillers naming one case
class RepoSourceOrder(Resolver): ...
class SprintTable(Renderer): ...

# ✓ mechanics that apply to everything they touch
class UserOrder(Resolver): ...
class Table(Renderer): ...
```

Nuance: "alphabetical" is fine as a mechanic a user *installs*. It stays wrong as the kernel's silent default, which decides without anyone choosing.

## Consequences

- Usable as a test in the full code review.
- **Named patterns** (the maintainer: "just composition and the strategy pattern applied to our plugin architecture"): the whole shape is POSA's **Microkernel**; resolvers, renderers, settings sources and `Dispatch` fillers are **Strategies**; wrappers are **Decorators**/middleware; words and grammar are **ports**, engines **adapters** (hexagonal). Eclipse/OSGi extension points and pluggy hooks are close prior art.
- **Service Locator caution:** `ask(RepoSource)` has the shape of a Service Locator, criticised for hiding dependencies. The answer recorded here was "plugins declare what they use (`uses-missing` quarantine)"; see the note above for the current answer. The twist that is ours: the kernel applies its own mechanism to itself and never decides. This must be documented.
