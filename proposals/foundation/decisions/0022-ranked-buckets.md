# 0022. Ranked buckets

- **Status:** amended · 2026-11-05 (afternoon) · the maintainer's framing
- **Rests on:** [0020](0020-core-is-the-kernel-only.md) core is the kernel only · [0021](0021-package-line-rule.md) package lines
- **Amended by:** [0046](0046-rank-3-parties.md) rank 3 is named "parties" · [0047](0047-architecture-vs-design.md) the buckets are conventions we keep for ourselves, not architecture: the kernel never enforces them

## Problem

Each placement question ("where does `Output` go? where does `Capacity` go?") was being argued from scratch. The maintainer noticed what the discussion had been circling: a set of ordered buckets, each piece swappable at a different cost.

## Decision

- Everything sits in **ordered buckets**. For any new thing, decide which bucket (existing or new) by the cost of placing it there and of replacing it there.
- The cost of replacing a piece is **how much rests on it**, so it grows the lower its bucket is.
- The shape is a **ranked DAG** (POSA's relaxed layered system; Clean Architecture's dependency rule). Arrows mean "depends on / refers to". They point **down**, may **skip ranks** (jira → `Record`), may point **sideways within a rank without cycles** (`Command` → `Output`), and **never point up**.

| Rank | Bucket | Holds | Swapping one costs |
|---|---|---|---|
| 0 | kernel | how plugins find and call each other | a different ecosystem |
| 1 | words | meaning (content words, categories) | everything referring to it needs bridges or new grammar |
| 2 | grammar | how words connect | a new language; words unchanged |
| 3 | parties (see 0046) | the language in use: code that does things | engine: a choice by resolvers; user: nothing rests on it |
| 4 | bundles | choosing compatible sets of packages | install a different set |

- Each bucket has rules for what it **accepts**, what it **rejects**, and what holds **within** it ([0023](0023-words-bucket.md), [0024](0024-grammar-bucket.md), [0025](0025-parties-bucket.md), [0026](0026-kernel-stripped.md), [0034](0034-bundles-definition.md)).
- Placement inside the frame: the kernel only if the kernel reads it; else meaning or doing; meaning standing alone goes to words, relating goes to grammar; place as high as it can work; package lines within a rank follow [0021](0021-package-line-rule.md).

## Rejected

- **Strict layering** (each rank rests only on the next one down): jira's commands would need to reach `Record` through grammar for no reason.

## Consequences

- Surfaced at once: **`Command` is grammar** (it relates inputs, output, settings and the run), which is why nothing pointed back at it. Contracts and slots are grammar too.
- Rank names went through "engines and users", "doing", "speeches", then "parties" (0046).
