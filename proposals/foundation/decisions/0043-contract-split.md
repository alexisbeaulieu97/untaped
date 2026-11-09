# 0043. `Contract` splits: mechanism vs language

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0024](0024-grammar-bucket.md) the grammar bucket; [0026](0026-kernel-stripped.md) the kernel stripped

## Problem

Review finding 3a, the kernel/language seam. The grammar guarantees (G1, G3, G5, G6) and `Provider[S].settings` need the notation (pydantic) and the settings words. The kernel is standard library only and knows no words. So a single `Contract` class in the kernel can't carry them.

Fact: today's `Contract.__init_subclass__` runs the signature checks, and `sdk/contracts/_declare.py` imports pydantic, records, errors, settings keys, the cache and progress.

## Decision

`Contract` splits in two:

- **the kernel's `Contract`** is the mechanism only: offered, filled, asked, resolved;
- **untaped's language `Contract`** (in the call grammar, `untaped-call`) subclasses it and checks G1/G3/G5/G6 itself when a contract class is created.

```python
# kernel: stdlib, no words
class Contract: ...                                   # routing only

# call grammar (untaped's language)
class Contract(kernel.Contract):                      # checks G1/G3/G5/G6
    ...

class RepoSource(untaped_call.Contract):              # ✓ a plugin's contract
    def repos(self) -> list[Repo]: ...
```

`Provider[S]` becomes the settings words' `Configured[S]`, usable by any class with settings, commands included. `.settings` asks `settings_store`.

## Consequences

- A rival language builds its own contracts on the kernel's.
- Leans toward review finding 6 (meaning may hold pure code: checks live in the grammar package).
- Where those checks run moved later: G3 is checked on `Loaded` ([0049](0049-kernel-hooks-loaded.md)), because a class-creation check can't see a word defined later in the same file.
