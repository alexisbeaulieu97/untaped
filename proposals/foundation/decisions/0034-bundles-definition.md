# 0034. Bundles: a definition plus conventions

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0014](0014-bundles-words-never-name-engines.md) bundles; words never name an engine · [0029](0029-resolvers.md) resolvers
- **Amends:** 0014 (the draft bundle rules B1–B3, BX1–BX3, BW1–BW2 are replaced)

## Problem

The draft bundle rules were many: dependencies only, floors only, no entry points, no picks or settings, not depended on by plugins (BX3), exactly one engine per slot (BW1), tested as a set (BW2), extras allowed if every combination passes (B3). After resolvers, some protected nothing, and the maintainer objected that calling "works out of the box" a **rule** is strong when the kernel catches it anyway.

## Decision

A lens for every bucket:
- **definition:** what puts something in the bucket;
- **rule:** only where a violation would go unnoticed, or quietly break someone else;
- **convention:** where a check or the kernel already reports the violation with facts.

Applied to bundles:
- **Definition:** a package with only dependencies and no entry points. One with an entry point is a plugin. It is invisible at run time; it chooses by including.
- **Conventions** (each violation is already reported elsewhere):
  - floors only → pins make pip/uv refuse and name the conflict;
  - works out of the box, tested at the lowest and newest versions with all extras → otherwise the kernel reports `Unfilled`/`Ambiguous`;
  - ships no settings or picks → nothing would read them.
- Optional groups (extras) are allowed. Anyone can publish a bundle.
- **A plugin depending on a bundle that contains an engine is allowed, but visible** (V1 in [0025](0025-parties-bucket.md)): the swap still works through resolvers; the cost is a heavier install and an idle engine's commands, settings and doctor checks. Doctor reports engines chosen for nothing as idle.

## Rejected

- **BX3, "bundles are not depended on by plugins":** after resolvers it no longer protects correctness.
- **BW1/BW2/B3 as separate rules:** resolvers don't solve them (a bundle can't carry preferences, and resolvers fill nothing), but they are all what the release test checks, so they merged into the one convention "works out of the box".

## Consequences

- Apply the definition/rule/convention lens to the other buckets during the full code test.
- Placeholder layout: `untaped` (the bundle most people install) = kernel + the shared words and grammar packages + CLI + settings engine + renderers; bigger bundles (e.g. `untaped-devops`) add git, git-cli, github, ansible, awx, ….
