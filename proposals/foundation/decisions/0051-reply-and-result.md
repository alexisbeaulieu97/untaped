# 0051. Extend results: `Reply` and `Result`

- **Status:** accepted · 2026-11-06 · the maintainer
- **Rests on:** [0044](0044-extend-answers-merge.md) extend answers; [0050](0050-dispatch-question-hook.md) `Dispatch` is a question hook
- **Amends:** [0044](0044-extend-answers-merge.md) and [0031](0031-call-grammar.md): the call grammar's `Answer`/`Failed` are replaced.

## Problem

With `Dispatch` empty, the kernel makes the extend call itself. But the signed `Answer` and the `Failed` note ([0044](0044-extend-answers-merge.md)) live in the call grammar, which the kernel can't import. What does an extend call return?

## Decision

Options compared:

- **R1, kernel facts:** chosen.
- R2, raw values: one failure sinks the rest; no signature.
- R3, whatever the dispatcher returns: askers would depend on the installed dispatcher.

The maintainer asked whether it is like Rust's `Box` (no: that is about where a value lives) or Rust's `Result` (yes).

**One `Reply` per provider:** who answered, and a `Result`.

```python
Reply(provider, Ok(value))
Reply(provider, Err(error))
Result = Ok[T] | Err
```

- `Reply`, `Ok` and `Err` are the kernel's own stdlib types, like `Unfilled` and `LoadProblem`. "Which provider gave which value or error" is a fact only the kernel has.
- Dispatchers return the same types, so askers see one shape whoever carries the call.
- Failures are data; one provider failing never sinks the rest.
- `Result` stays separate from the signature, because "value or failure" recurs elsewhere (a runner's exception travelling back to a hub, even for a slot call).

Slot calls: `Ok` → the value; `Err` → raised to the asker (settled with the `Call` shape, [0052](0052-call-shape.md)).

## Consequences

- The kernel's `Err` can carry any plugin's error word (`Declined`, `TokenExpired`) without knowing what it means: the kernel gives the envelope, the grammar gives what's inside.
- Merging stays the call grammar's ([0044](0044-extend-answers-merge.md)).
- `Reply.provider` became a `PluginId` in [0052](0052-call-shape.md).
