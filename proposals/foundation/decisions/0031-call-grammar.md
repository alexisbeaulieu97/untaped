# 0031. A call grammar package

- **Status:** amended · 2026-11-05 · the maintainer
- **Rests on:** [0024](0024-grammar-bucket.md) the grammar bucket · [0028](0028-wrappers-leave-the-kernel.md) wrappers leave the kernel
- **Amended by:** [0051](0051-reply-and-result.md) the call grammar's `Answer`/`Failed` are replaced by the kernel's `Reply` and `Result`

## Problem

G6 says every call is a question or an action, and wrappers act on that mark. Where do `Question`, `Action` and `CallWrapper` live? If in the kernel, the kernel holds vocabulary; if nowhere shared, wrappers can't read the marks. And the kernel's own contracts can't carry marks that live outside it.

## Decision

- `Question`, `Action` and `CallWrapper` live in a **call grammar** package (placeholder `untaped-call`), separate from the kernel and from the dispatcher engine (`untaped-calls`, which fills `Dispatch`).
- The kernel calls its own extension points directly. They are **never dispatched** (dispatching them would loop through resolving `Dispatch`) and carry no marks.
- The kernel's `Call` carries no marks; wrappers read them from the method.

```python
# ✗ the kernel interprets a mark
Call(point, method, marks=frozenset({Question}))

# ✓ the mark sits on the contract's method; wrappers read it there
class RepoSource(Contract):
    @question
    def repos(self) -> list[Repo]: ...
```

## Rejected

- **Marks in the kernel** (passed through, never interpreted): vocabulary in the kernel, against [0020](0020-core-is-the-kernel-only.md).

## Consequences

- The names `untaped-call` / `untaped-calls` were flagged by the design review as easy to confuse (simplification finding 16; not yet renamed).
