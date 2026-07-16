
Independent tool environments all read and write the same
`~/.untaped/config.yml`, so its format is a contract. It was declared **v1** and
**frozen and stable across all `untaped` SDK 1.x releases**; any change was a
major (2.0) SDK event. SDK **2.0** is that event: it moved `http` and `ui` out
of the top-level "SDK globals" position and into ordinary per-profile settings
(see below). The format is now **v2**, the contract for SDK 2.x.

The v2 surface:

- Top-level keys: `active:`, `profiles:`, plus tool-owned top-level **state**.
- `http` and `ui` are **per-profile** keys, addressed by dotted name
  (`http.verify_ssl`, `ui.theme`), living under `profiles.<name>.http` /
  `profiles.<name>.ui`. A top-level `http:`/`ui:` block is ignored.
- One section per tool, holding that tool's profile fields plus its disjoint
  top-level state fields (the two field sets must not overlap).
- Profile layering: `profiles.default` sits beneath `profiles.<active>`.
- Env-var override shape: `UNTAPED_<SECTION>__<FIELD>` (uppercased section,
  double underscore before the field).
- Bare config keys address the invoking tool's own section (e.g.
  `untaped-github config set token X` writes `github.token`).

**What changed in 2.0 and why.** In SDK 1.x, `http` and `ui` lived at the top
level via a separate "state section" mechanism and were treated as cross-cutting
globals, so `config set http.* / ui.*` wrote top-level and rejected
`--target-profile`. The reasoning was that HTTP behaviour and terminal
presentation were user-wide concerns. In practice they vary by
environment — a `work` profile needs a corporate CA or a relaxed hostname check
while a personal profile doesn't — so 2.0 makes them per-profile like every
other setting: they layer through `profiles.default` → active profile and honour
`--profile`/`--target-profile`. The top-level "state section" mechanism still
exists, but now only for per-tool `state_model`s (e.g. `workspace.workspaces`,
`ansible.sources`), which legitimately are top-level tool-managed data.

Each tool validates only its own section (`extra="ignore"`) and writes are
surgical and locked, so an older tool never clobbers a newer tool's section.
