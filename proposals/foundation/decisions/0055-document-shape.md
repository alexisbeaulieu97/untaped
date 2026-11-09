# 0055. The shape of this document

- **Status:** accepted · 2026-11-07 · the maintainer
- **Rests on:** [0047](0047-architecture-vs-design.md) architecture vs design

## Problem

The working files mixed two things:

- **rules:** what is true now (a plugin author needs them; they should stay short and current);
- **decisions:** why it is true and what was turned down (a maintainer reopening something needs them; they only grow).

The snapshot was mostly rules and the journal mostly decisions, but each leaked into the other. Text went stale, and the maintainer couldn't remember why a piece existed.

## Decision

**B: a folder of rule pages plus decision records.**

```
README.md          purpose, philosophy, the tower picture, how to read this folder
architecture.md    the kernel
design/            conventions, then one page per first-party plugin design
decisions/         NNNN-slug.md, one per decision
glossary.md
```

- A rule is present tense and short, and links the decision that made it.
- A decision states its problem, the decision, what was rejected, its consequences, and what it **rests on**. It is never rewritten: a later record supersedes or amends it, with a link both ways.
- "Rests on" makes the tower visible: when a decision changes, you can see what sits on it.
- A decision has the parts AGENTS.md asks of a feature design: problem, approach, rejected alternatives, impact.

**Open questions are not in it.** AGENTS.md: the repository keeps no backlog; they become GitHub issues.

## Rejected

- **A, one file in two parts** (architecture, then design): the "why" leaks into the current truth or gets lost, and the tower isn't visible.
- **C, decisions only:** the current truth can only be found by replaying the log.

## Consequences

- The maintainer chose to build it now and record everything, not convert at the end.
- Location in the repository (`docs/foundation/`?) is not yet confirmed.
