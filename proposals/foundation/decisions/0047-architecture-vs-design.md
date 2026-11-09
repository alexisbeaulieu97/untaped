# 0047. Architecture vs design

- **Status:** accepted · 2026-11-06 · the maintainer
- **Rests on:** [0022](0022-ranked-buckets.md) ranked buckets; [0026](0026-kernel-stripped.md) the kernel stripped

## Problem

While reopening finding 4 ([0045](0045-two-kinds-of-order.md)), the maintainer lost track of why pieces existed (why resolvers?). We had been mixing three levels: the kernel, the rules we keep for ourselves, and one plugin's own design. The maintainer went back to the roots: technically everything is a plugin unless it touches the kernel; the buckets are conventions nobody has to follow.

## Decision

**Architecture** is only what is in the kernel: what changes the kernel's code. It is there out of necessity, mandatory for everyone, and adding to it is a deliberate decision.

**Design** is everything chosen on top, all swappable:

- **conventions:** the buckets (words, grammar, parties, bundles), arrows down, the notation and G1–G6, "words never name an engine", the run rule, the mechanics convention, naming;
- **packaging:** which package holds what, what becomes its own plugin;
- **first-party plugins:** resolvers, settings, calls.

Every division is conceptual. Everything could ship in one PyPI package and work the same; the only real effects of a split are release versioning and what a user can install separately. We split where we foresee a reusable mechanic. Resolvers exist because we judged them useful: without them the kernel still works and reports more `Ambiguous`.

**The kernel never enforces conventions.** It knows no words, and a plugin runs with the user's rights (no guards that only look like protection). Conventions get teeth only from plugins (an owner checking its own language) and from our repository's tests. A plugin that ignores them still works; it only gives up what they buy.

Our conventions should be solid enough that people think twice before starting their own.

## Consequences

- The document has two parts: architecture (the kernel), then design, labelled as such ([0055](0055-document-shape.md)).
- Settings-source order is the settings plugin's design. The only architecture question left in finding 4 is whether `Resolver` declares what it orders ([0045](0045-two-kinds-of-order.md)).
- Each kernel piece is checked against the entry test ("without it, plugins couldn't find or call each other"); `Dispatch` and `OwnerCheck` were the first suspects ([0049](0049-kernel-hooks-loaded.md), [0050](0050-dispatch-question-hook.md)).
