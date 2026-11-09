# 0032. Picks and provider order belong to resolvers

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0029](0029-resolvers.md) resolvers
- **Amends:** [0012](0012-two-engines-refuse.md) ("the pick is an ordinary setting in the owner's section" no longer holds)

## Problem

Two kinds of choice were still stored in words owners' settings:
- per-slot picks (`git.repo_store.pick`);
- the order of extend providers whose answers overlap (two providers listing the same repo): today `git.extensions.repo_source.rank`, repeated by each asker (`ansible.extensions.checkout_source.rank`).

Under resolvers, both are resolution plumbing inside words.

## Decision

1. **Per-slot picks belong to the resolvers**, not to the slot owner's settings. Words say nothing about how engines are chosen; each resolver designs how its users declare choices (the shape is decided with the settings questions).
2. **Extend providers are ordered only by resolvers** (`Resolver.order`, same agreement rule as slots). With no agreed order the kernel hands back a set, and an asker that hits overlapping answers reports `Ambiguous(item=…, providers=…)` instead of guessing.
3. The per-owner and per-asker `rank` settings go away.

```python
# ✗ resolution plumbing in words
class GitSettings(Settings):
    repo_store_pick: str
    repo_source_rank: list[str]

# ✓ words say nothing; a resolver the user installed decides
class UserOrder(Resolver): ...
```

## Consequences

- A result never depends on install order.
- The maintainer's caution that followed (don't make resolvers for everything) became the mechanics convention: [0033](0033-mechanics-convention.md).
- How an asker combines answers without naming providers: [0044](0044-extend-answers-merge.md).
