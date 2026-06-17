# Architecture decisions

A short record of the decisions behind the SDK-only direction. These are
settled; this page is the reference, not a discussion.

## 1. `untaped` is an SDK, not an app — plugins retired

`untaped` is a batteries-included CLI *framework* (config, profiles, themes,
consistent output, piping, HTTP/UI helpers) built on cyclopts. There is no
central `untaped` command, no plugin platform, no managed virtual environment,
no shim, and no install script.

Each tool is an **independent CLI** that depends on the `untaped` SDK and is
installed in its own `uv tool` environment:

```bash
uv tool install git+https://github.com/alexisbeaulieu97/untaped-github.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-ansible.git
```

PyPI publishing is deferred, so tools currently install and depend on the SDK
via git links rather than PyPI package names.

The suite is: `github`, `jira`, `awx`, `ansible`, `workspace`, `apple-health`.

Tools are versioned, installed, and released independently — possibly against
different SDK versions — but they **share two contracts**: the
`~/.untaped/config.yml` config format (see #4) and the `--format pipe` envelope
(see #3). This coupling is accepted and deliberately frozen so independently
installed tools interoperate.

## 2. Profiles and themes are absorbed into the SDK

Profile resolution and the profile management group (`create`/`list`/`use`/
`delete`), plus the built-in theme presets, are now part of the SDK and mounted
per tool by `run_tool`. The standalone `untaped-profile` and `untaped-themes`
packages are **retired** — a standalone tool would reintroduce "install one
more thing to manage shared state".

## 3. Pipe envelope v1 — frozen across SDK 1.x

The `--format pipe` envelope (NDJSON; see [`src/untaped/pipe.py`](../src/untaped/pipe.py))
is declared **v1** and is **frozen and stable across all `untaped` SDK 1.x
releases**. Any change to the envelope shape is a major (2.0) SDK event.

Tools pipe across separate `uv tool` environments that may run different SDK
versions, so the envelope is a cross-tool wire contract: this freeze is what
guarantees `untaped-github | untaped-ansible` works regardless of which 1.x SDK
each tool was built against.

## 4. Config format — v1 (SDK 1.x) → v2 (SDK 2.x)

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

## 5. The SDK ships no core agent skill

The SDK does **not** package a core agent skill. The old
`skill_assets/untaped/SKILL.md` taught the retired plugin CLI and is obsolete.
Per-tool `SKILL.md`s (installed via each tool's `skills` group) cover tool
usage, and guidance on building tools with the SDK lives in the README and
`docs/`, not in a packaged agent skill. (The file is removed later, in Phase 3;
this entry only records the decision.)
