# 0053. The kernel's founding principle

- **Status:** accepted · 2026-11-06 · the maintainer
- **Rests on:** [0047](0047-architecture-vs-design.md), [0048](0048-kernel-requirement-rule.md), [0049](0049-kernel-hooks-loaded.md), [0050](0050-dispatch-question-hook.md), [0051](0051-reply-and-result.md), [0052](0052-call-shape.md)

## Problem

The kernel had five rules and several hooks, each settled on its own. Nothing said what they have in common, so there was no test for the next piece and no way to check the pieces against one idea.

## Decision

The maintainer's framing: **"a courier protocol built in Python."**

- The kernel is a protocol. It doesn't know where it runs or what it has access to; its only opinions are its shapes.
- **It routes by types:** contracts, slots and their fillers are typed objects, never names (the maintainer: words, grammar, parties are all types).
- Its job is to keep a **valid state at all times**. An ambiguity or an error is a valid state.

**Valid** means:

1. every operation ends in exactly one known outcome: a value, or one of the kernel's facts (`Unfilled`, `Undecided`, `Ambiguous`, `Loop`, `NoScope`, `Err`, `LoadProblem`);
2. the kernel's own records (what's loaded, what fills what, the open scopes) are never left half-changed.

**The protocol's parts:**

| Protocol | Kernel |
|---|---|
| addresses | `offers` / `fills` |
| the package | `Call` |
| the receipt | `Reply` |
| the courier | `Dispatch` |
| which address, if several | `Resolver` |
| a delivery round | `scope` |
| returned to sender | the facts |

## Consequences

- Kernel rules 3, 4 and 5, failures as data, and the multi-host guardrail read as consequences of the principle. Restating the rules that way is pending.
- **Stress test, one concern at a time** (the maintainer slowed me down when I listed three new duties at once):
  1. recording a plugin is all or nothing → settled in [0054](0054-recording-is-atomic.md);
  2. a plugin misbehaving inside a hook becomes a fact → pending;
  3. leaving a scope restores the previous state → pending.
