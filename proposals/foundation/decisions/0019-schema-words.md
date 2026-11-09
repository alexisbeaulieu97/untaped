# 0019. Schema words and a converter slot

- **Status:** superseded by [0020](0020-core-is-the-kernel-only.md) on 2026-11-05 · originally settled before 2026-11-05 (7.8; two items were only [settled?]) · the maintainer
- **Rests on:** [0005](0005-core-option-c.md) core = option C; [0018](0018-zero-dependency-core.md) core has no dependencies
- **Replaced by:** [0020](0020-core-is-the-kernel-only.md). Most of this dissolved: no schema slot, no conformance kit, no `load`/`Json`/`Clean`/`@check`/`UNSET`.

## Problem

Under C, core owned data-shaped words but could not depend on pydantic ([0018](0018-zero-dependency-core.md)). Data still had to be converted and validated somewhere.

## Decision (as it was)

- **Key fact:** core itself never converts anything. Every conversion happens at an edge, and every edge is an engine (front ends, the config engine, renderers).
- The conversion rules are meaning, owned by a **words package `untaped-schema`** plus a **conformance kit** (tests every schema engine must pass). Converting is an engine filling a **replace slot**, with pydantic as the default engine.
- **Verbs:** `load(T, data) -> T`, `dump(value) -> Json`, `describe(T) -> JSON Schema`, as free functions using the current run's engine. Prior art agrees: cattrs, msgspec, serde, Go `encoding/json`, Jackson, JSON-B; pydantic is the outlier.
- **Constructors never convert:** `Sprint(id="5")` stores `"5"`. Outside data enters only through `load`.
- **`Json` type** [settled?]: the words that bring data in return `Json`, so type checkers refuse to pass it to a constructor (verified with pyright and mypy).
- **`@check`** [settled?]: pure, refuse-only functions that core calls when an object is built.
- **`Clean(fn)`** for cleaning at `load` (replacing a proposed `Strip` mark: "Strip is a verb, not a word"), declarative constraint marks, an `UNSET` sentinel, a keyword-only `Field`, one core error type with a field path.

How the checks were chosen: three designs were compared (core checks; edges check; the `Json` convention plus `@check`). Three independent designers rejected "edges check": validity would depend on the run's engine, real AWX code builds objects between edges, and each edge pays again. They preferred core checks, but its shallow type checks kept accumulating edge cases (`bool` is an `int`, list items, nested objects, `Literal`, forward references). The maintainer objected to "an ever-growing list of edge cases".

## Rejected (at the time)

- **Accepting pydantic as a core dependency.** It would have been convenience, not necessity, and core's first exception to "no engine".

## Why it was replaced

The maintainer restarted this and asked *why* untaped needs to describe, convert, check and clean data at all. The answer: only where a generic engine handles data on a plugin's behalf, at untaped's own boundaries (command inputs, settings, records, errors and messages). A plugin's own data is its business. Most of this machinery (`load` in plugin code, `Json`, `Clean`, `UNSET`, the `Sprint(**row)` hole) came from mixing the two kinds.

What was left was one real tension: boundary data still needs a library like pydantic, and under C, core owned those words. The pile of special cases traced back to C's false premise ([0005](0005-core-option-c.md)). Lesson: accumulating special cases mean a premise is false. See [0020](0020-core-is-the-kernel-only.md).
