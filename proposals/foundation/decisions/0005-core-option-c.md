# 0005. Core keeps the shared words (option C)

- **Status:** superseded by [0020](0020-core-is-the-kernel-only.md) on 2026-11-05 · originally settled before 2026-11-05 (foundation point 4; the maintainer: "Perfect.") · the maintainer
- **Rests on:** [0002](0002-words-engines-users.md) words, engines, users; [0004](0004-words-without-a-center.md) words without a center
- **Replaced by:** [0020](0020-core-is-the-kernel-only.md). Core is the plugin kernel only (option B).

## Problem

What does core hold?
- **A.** What every plugin needs (AGENTS.md today).
- **B.** Only what lets plugins find and use each other (dsh). Even what a command is lives in plugins.
- **C.** Apply words/engine to core itself. Core keeps the words every plugin speaks, and the engines behind them (the CLI, the config reader, the renderers) become swappable default plugins.

## Decision (as it was)

**C.** Core contained:
- **the plugin kernel:** `Plugin`, `Contract`, `Provider`, the replace slot; loading, quarantine, reading the picks; `Issued`/`Source`/bridges; stability marks;
- **five families:**
  - Command: inputs and outputs, consent, hints;
  - Record: kinds, outcome and check rows;
  - Settings: `key(Model).field` references and a secret mark;
  - Errors and messages;
  - Run;
- **judgment-call defaults:**
  - CLI hints stay as hints that other front ends ignore;
  - a few primitive value types stay;
  - "where a plugin keeps files" goes to a storage plugin;
  - projects and profiles go to the config engine;
- **a layout rule:** the kernel never imports the families, checked by a test. This keeps it extractable.

Why C over B, at the time:
- every plugin needs the families anyway, and a package everyone must depend on is core with extra steps;
- core can validate plugins at load, while B would need a generic hook;
- the startup cache stays simple;
- core reads the picks before plugins load;
- there is one command vocabulary;
- the migration is smaller;
- C → B stays possible later.

The deciding remark: the maintainer wants TUI, GUI and web front ends, and C covers them as engines over the same words.

What C blocked: redefining Command, Record, Settings or Errors (a change to them is a core major), and using core for an unrelated product. Most redefinitions examined were additions or engines. The real redefinitions clustered into three themes: plugins not written in Python, long-lived multi-user servers, and localization.

## Rejected (at the time)

- **A:** wins only if engines never need to be swapped.
- **B:** see the reasons above.

## Why it was replaced

C assumed that **core can own data-shaped words without owning the doing behind them.** That turned out to be false. Boundary data (command inputs, settings, records, errors) needs conversion and validation, and doing that well takes a library like pydantic. Under C, core either depended on pydantic or rebuilt it; [0019](0019-schema-words.md) was the attempt to rebuild it, and it kept growing special cases. When the maintainer asked *why* untaped needs to convert data at all, every decision was re-run under B. See [0020](0020-core-is-the-kernel-only.md).

What would have made C win:
- the families could be pure definitions with no library behind them; or
- the kernel genuinely needed to read commands or settings itself.
