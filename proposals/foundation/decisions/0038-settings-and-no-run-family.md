# 0038. Settings through a slot; a run is a scope

- **Status:** amended · 2026-11-05 · the maintainer
- **Rests on:** [0008](0008-the-run-rule.md) the run rule · [0026](0026-kernel-stripped.md) the kernel stripped · [0037](0037-concurrency.md) concurrency
- **Amended by:** [0049](0049-kernel-hooks-loaded.md) the settings owner gathers declarations on the `Loaded` moment, not through `OwnerCheck`
- **Amends:** [0008](0008-the-run-rule.md) (the rule made concrete) · [0037](0037-concurrency.md) (`Capacity` is delivered the same way)

## Problem

The first proposal had settings declared (`Provider[S]`, `Command[S]`) and supplied by the run. The maintainer asked two things: how does the settings engine discover who needs settings? And if the run carries them, github and settings can't live on their own: they need a run to talk.

## Decision

**Settings:**
- **Discovered from declarations**, no run needed: `Provider[GitHubSettings]` is a declaration the kernel already stores; the settings owner reads it. The settings engine and doctor know every section before anything runs.
- **Delivered through a slot** the settings words offer (`settings_store`), filled by the settings engine. `Provider.settings` asks it. github depends on the settings words only.
- **Per-user variation** comes from a **kernel scope** (stdlib `contextvars`) in which `settings_store` resolves to that user's instance. Resolving within a scope is part of resolving slots.

**There is no Run family.** A run is a kernel scope that a host opens, binding fillers of other owners' slots: settings (`settings_store`), messages and progress (messages words), questions (prompts words), cancellation and `Capacity` (concurrency). The `Context` object disappears; the command grammar may offer shortcuts (`self.progress(...)`).

**The run rule, made concrete:** everything per-run is reached through a slot resolved in the scope, never a module global.

```python
# ✗ a run object carrying everything; a module global
settings = current_run().settings["github"]

# ✓ a slot resolved in the scope the host opened
with scope(settings_store=UserSettings(alice), capacity=Capacity(8)):
    ask(RepoSource).repos()
```

## Consequences (sweep)

- Families: five → **four**: Command (grammar), Record, Settings, errors and messages; plus new packages for concurrency and prompts.
- "Core offers 'start a run with these settings and this way to talk to the user'" becomes "a host opens a kernel scope".
- github, settings and the hub each stand alone. The maintainer: "a sign that the architecture is working properly".
- Raised and settled next: what is bound in a scope and what happens outside one ([0040](0040-scope-bindings-noscope.md)); this is the third or fourth implicit thing turned into a slot, which led to the parked "ambient inputs" question.
