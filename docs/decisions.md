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
uv tool install untaped-github
uv tool install untaped-ansible
```

PyPI package metadata is the release contract. Suite repos carry no standing
`[tool.uv.sources]` git pins (dropped 2026-07-02 once the suite was on PyPI);
release artifacts are still built with `uv build --no-sources` so wheels
declare only package ranges such as `untaped>=3.0.0,<4`.

The suite is: `github`, `jira`, `awx`, `ansible`, `workspace`, `recipe`,
`apple-health`.

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

## 3. Pipe envelope v1 — versioned independently of the SDK

The `--format pipe` envelope (NDJSON; see [`src/untaped/pipe.py`](../src/untaped/pipe.py))
is declared **v1** and is **frozen and stable across SDK 1.x *and* 2.x**. The
envelope carries its own version number (`"untaped": "1"`) and is versioned
independently of the SDK: it did not change when the SDK went to 2.0 (that was a
config-layout break, not a pipe break). Any change to the envelope shape would
bump the envelope version, regardless of the SDK version it ships in.

Tools pipe across separate `uv tool` environments that may run different SDK
versions, so the envelope is a cross-tool wire contract: this freeze is what
guarantees `untaped-github | untaped-ansible` works regardless of which SDK
version each tool was built against.

Record fields remain producer-owned, but filesystem mutation consumers need a
generic target contract that does not require understanding another tool's
domain. When a pipe record names a concrete filesystem target, producers put it
in `record.target_path` as an absolute, non-empty path. Producers omit
`target_path` when no concrete target exists; they do not emit `""` or `null` as
a target. Domain fields such as `path`, `workspace`, `repo`, or `full_name` may
remain for display and templating, but consumers should not branch on a
producer-specific `kind` just to locate the target. Pipe records whose `kind`
ends in `.summary` are informational summaries rather than filesystem targets,
and target consumers may skip them. This is a record-level convention and does
**not** change the v1 envelope shape.

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

## 6. `emit` detail-routing and safe HTTP retries (SDK 2.1)

Two additive surface changes in SDK 2.1, both about making the common case
correct by default.

**`emit` routes a single entity to a detail view.** Single-entity commands
(`whoami`/`get`/`show`/`status`) used to render their one object as a one-row
table via `echo(render_rows([x.model_dump()], …))` — a wide, awkward shape, easy
to forget the `echo` (silent no output), and a manual `model_dump()`. `emit`
dispatches by shape: a single pydantic model or `Mapping` renders as a vertical
`key: value` **detail** view (reviving the previously-unused
`UiContext.detail()`), while a sequence renders as a **collection**. It accepts
models directly and writes stdout itself. Under structured formats this also
fixes the single-entity JSON shape: a tool that adopts `emit` emits a bare
object `{…}` instead of a one-element array `[{…}]` for a single entity.
`--format pipe` is unchanged (the per-record envelope is identical to
`render_rows`), so the pipe contract from #3 is untouched. `render_rows` stays
for explicit row collections.

**`connected_client` retries transient failures by default.** A new public
`RetryPolicy` (a frozen dataclass) backs retries in `HttpClient`, and
`connected_client(...)` enables a safe default `RetryPolicy()` automatically
(`retry=None` disables it; a custom policy overrides). The policy is deliberately
conservative so an automatic default can never silently double a non-idempotent
write:

- **Transport failures are phase-gated.** A *pre-send* connect failure never
  reached the server, so it is retried for any method; a *post-send* read/write
  error may already have been processed, so it is retried only for idempotent
  methods. This distinction is what makes retrying-by-default safe.
- **`429`/`503` retries are idempotency-gated.** They apply only to
  `idempotent_methods` (`GET`/`HEAD`/`OPTIONS`/`PUT`/`DELETE` by default). A
  caller whose `POST` is genuinely idempotent (a search endpoint) opts in by
  passing a `RetryPolicy` whose `idempotent_methods` includes `"POST"`.
- `Retry-After` (seconds or HTTP-date) is honored up to `retry_after_max`;
  otherwise the delay is exponential backoff capped at `backoff_max`.

**`paginate_offset` forwards a per-call `retry=` (2.2.0).** The POST opt-in above
only reaches a single request; collection walks go through `paginate_offset`,
which previously fetched each page with the client's default policy and no way to
override it. It now takes a `retry=` (default `_INHERIT`) and forwards it to every
page fetch, so a tool can make just its idempotent search endpoint retry without
making any other `POST` (e.g. a create) retryable. Additive; existing callers that
omit `retry=` are unchanged.
