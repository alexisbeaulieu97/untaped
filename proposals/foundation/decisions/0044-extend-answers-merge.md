# 0044. Extend answers; askers merge, never name

- **Status:** amended · 2026-11-05 · the maintainer
- **Rests on:** [0003](0003-extend-and-replace.md) extend and replace; [0032](0032-picks-and-order-are-resolvers.md) order belongs to resolvers; [0043](0043-contract-split.md) the `Contract` split
- **Amended by:** [0051](0051-reply-and-result.md): the call grammar's `Answer`/`Failed` are replaced by the kernel's `Reply(provider, Result)`. Merging stays as decided here.

## Problem

Review finding 3b: who fans out an extend call, and what comes back? The kernel can't attach the call grammar's types.

## Decision (as settled)

An extend call returns **one entry per provider**: a signed `Answer` (the value plus who answered) or a `Failed` bounce note. Failures are data; one provider failing never sinks the rest. With nothing filling `Dispatch`, the kernel calls providers in a fixed, meaningless order; the dispatcher engine runs them in parallel.

**The asker never names a provider.** The maintainer asked: how does ansible pick, and how does it avoid knowing github and gitlab exist? My first example was wrong:

```python
by_provider[github].repos          # ✗ ansible would have to know github exists
```

```python
merge(answers, by=key(Repo).name)  # ✓ the call grammar's generic, pure merge
```

- The asker chooses the key; the key defines what an **overlap** is.
- Items only one provider lists are kept.
- An overlap is settled by the resolvers' agreed order, or reported as `Ambiguous(item, providers)` for that item alone.

Signatures (who answered) serve the machinery and the user (merge order, `--columns +source`, failure messages), never the asker's logic.

## Consequences

- Per-owner and per-asker `rank` settings stay gone ([0032](0032-picks-and-order-are-resolvers.md)).
- "Settled by the resolvers' agreed order" rests on how resolvers order, which is reopened in [0045](0045-two-kinds-of-order.md).
