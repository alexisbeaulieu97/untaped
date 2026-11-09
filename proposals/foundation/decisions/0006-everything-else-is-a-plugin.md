# 0006. Everything else is a plugin

- **Status:** amended · settled before 2026-11-05 (foundation point 5; the maintainer: "Perfect.") · the maintainer
- **Rests on:** [0002](0002-words-engines-users.md) words, engines, users; [0005](0005-core-option-c.md) core = option C (superseded)
- **Amended by [0020](0020-core-is-the-kernel-only.md):** the families leave core too. They go to words packages written with pydantic, so this list now covers everything except the kernel.
- **Amended by [0026](0026-kernel-stripped.md):** stability marks, reading picks, profiles, settings, cancellation, caching and printing leave the kernel as well.

## Problem

Under option C ([0005](0005-core-option-c.md)), core kept the shared words. What happens to everything else core does today?

## Decision

- **Default swappable engines:** the CLI front end, the config reader (config.yml, env, profiles), the renderers (table, JSON, pipe) and message display.
- **Shared mechanics, each a set of words plus an engine:**
  - storage (cache, state, folders, locks);
  - HTTP;
  - secrets and credentials;
  - prompts and screens;
  - projects;
  - doctor checks;
  - skills;
  - docs.
- **Leaving core:** `Terminal`, prompts and screens, `Cache`/`StateRows`/`Dir`/`Lock`, `bounded_map`/`Parallel`, `HttpError`, credential stores and login, doctor checks, and `StubSkill` (which belongs to a skills plugin).
- **Plain libraries, not plugins:** text helpers (`plural`, `q`, `ago_text`), diff text, tree drawing.

## Consequences

- `bounded_map`, `bounded_iter` and `Parallel` later became the concurrency package's ([0037](0037-concurrency.md)).
- The test for "plain library or plugin" became part of the placement check ([0011](0011-placement-by-intent.md)): doing is a plugin if it fills words, must be swappable or configurable, or touches the run. Otherwise it is a plain library.
