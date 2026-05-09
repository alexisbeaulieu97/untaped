# AGENTS.md — `untaped-profile`

Meta-domain that owns the **profile inventory** in
`~/.untaped/config.yml` — which profiles exist, which is active, how
to create / use / show / delete / rename them. Sister meta-domain
[`untaped-config`](../untaped-config/AGENTS.md) operates on the keys
*inside* whichever profile is targeted; this package operates on the
top-level `profiles.<…>` blocks and the `active:` pointer themselves.
For workspace-wide rules and the cross-cutting helpers index, see the
[root `AGENTS.md`](../../AGENTS.md). For env/active/fallback
resolution mechanics, see
[`untaped-core/AGENTS.md` "Profile resolution (internals)"](../untaped-core/AGENTS.md#profile-resolution-internals).
For user-facing reference, see
[`docs/configuration.md`](../../docs/configuration.md).

## Inventory surface

Seven commands ship: `list`, `show`, `use`, `current`, `create`,
`delete`, `rename`. Each maps to one use case in `application/`,
which talks to a single concrete adapter (`ProfileFileRepository` in
`infrastructure/profile_repo.py`) via the `ProfileRepository`
Protocol declared in `application/ports.py`. The adapter delegates
every read and write to the profile-aware helpers in
`untaped_core.config_file` and `untaped_core.profile_resolver` — this
package does not parse or write YAML directly.

## Active vs persisted-active

`ProfileFileRepository` exposes **two** ways to read the active
profile name. They look near-identical and pick different sources on
purpose:

- `active_name()` returns the *effective* active profile, honouring
  `UNTAPED_PROFILE` and the root `untaped --profile <name>` flag.
  Read-side concerns use this so the world stays consistent during a
  per-call override — e.g. the ✓ marker in `untaped profile list`
  reflects whichever profile the current process is actually using.
- `persisted_active_name()` returns *only* the `active:` key on disk,
  ignoring per-call overrides.

The invariant: **a transient `--profile` flag must never rewrite the
user's persisted active pointer behind their back.** Mutating use
cases (today: `DeleteProfile`) compare against
`persisted_active_name()`, not `active_name()` — otherwise running
`untaped --profile staging profile delete staging` while `production`
was the persisted active would refuse the delete based on a transient
override, which is hostile. Future mutating use cases that touch the
`active:` pointer should follow the same rule.

For the env/active/default/schema layering itself, see
[`untaped-core/AGENTS.md` "Profile resolution (internals)"](../untaped-core/AGENTS.md#profile-resolution-internals).

## `current` and the source contract

`untaped profile current` returns more than a name: it returns
`(name, source)` where `source ∈ env / config / fallback`. The bare
name goes to stdout; `(source: …)` goes to stderr. Pipe-friendly by
construction.

The use case (`application/current_profile.py`) **validates** when
`source ∈ env / config`: the named profile must actually exist on
disk, otherwise it raises `ConfigError` listing the known profiles.
This protects the documented pipe pattern
`untaped --profile $(untaped profile current)` — without validation,
a typo in `UNTAPED_PROFILE` or `active:` would silently propagate
into a downstream `--profile` that other commands then reject with a
worse error, far from the source.

`fallback` (no env, no `active:`, or `active:` with no matching
profile) reports the conceptual `default` placeholder regardless of
whether `profiles.default` exists on disk — schema defaults are in
effect either way, and there's no user typo to protect against.

## Reserved-name and active-pointer invariants

A handful of small invariants are spread across the use cases; this
section pulls them together so adding a new mutating use case can
honour the same set:

- `RenameProfile` rejects renaming `default` and rejects `default`
  as the rename target (it's the implicit floor; renaming it would
  break the fallback layer). When the renamed profile is the
  *persisted* active one, `active:` is updated in the same
  `mutate_config` op via `untaped_core.config_file.rename_profile`
  — so the pointer never points at a missing profile mid-rename.
- `DeleteProfile` refuses to delete the persisted active profile
  (would orphan `active:`). `default` is **not** special-cased — when
  it's not active, deleting it just clears any shared overrides and
  values fall through to schema defaults.
- `CreateProfile` deep-copies on `--copy-from` so later edits to the
  source profile don't bleed into the new one. Empty names and
  already-existing names are rejected with the known-profiles list.

## Redaction (cousin to `untaped-config`)

`untaped profile show` redacts secrets at the **CLI layer**
(`cli/commands.py`) using
`redact_secrets(profile.data, secret_field_paths(Settings))` from
`untaped_core` — the dict-walking variant. Both `--format yaml` and
`--format json` redact; `--show-secrets` reveals.

[`untaped-config`'s `display_value`](../untaped-config/AGENTS.md#redaction)
is the row-rendering cousin: same intent, different shape (rows vs
nested dict). The two redactors are kept separate because their
inputs are structurally different — there's no useful shared helper,
and merging them would obscure both call sites.

## Layering

Standard 4-layer DDD per root AGENTS.md "Architecture: 4-Layer DDD".
Three package-specific notes:

- **`application/ports.py` already exists.** A single
  `ProfileRepository` Protocol (10 methods) satisfies every use case.
  This is currently the only domain in the workspace compliant with
  the rule that Issue #15 of the audit-driven plan hardens — when
  consolidating other packages, this is the reference shape.
- **One concrete adapter.** `ProfileFileRepository` is a thin pass
  through to `untaped_core.config_file` (`read_profile`,
  `write_profile`, `delete_profile`, `rename_profile`,
  `set_active_profile`, …) and `untaped_core.profile_resolver`
  (`classify_active_profile`, `effective_active_profile_name`,
  `resolve_profiles`). New profile-level operations belong as new
  helpers in core's `config_file` module, with this adapter
  delegating.
- **Per-command Format restrictions are inline.** `show` narrows
  `FormatOption` to `Literal["yaml", "json"]` because a single
  nested object has no rows for `raw`/`table` to render. New
  commands that only emit one shape should narrow the same way
  rather than accept a Format value they can't honour.

## Recipe: add a new profile sub-command

1. If the command needs an external operation the repo doesn't already
   expose, add a method to `ProfileRepository` in `application/ports.py`
   *and* implement it on `ProfileFileRepository`. Prefer adding a
   helper to `untaped_core.config_file` and delegating, rather than
   reading/writing YAML in this package.
2. Add a use case in `application/`. Take `ProfileRepository` via
   constructor injection. For mutating use cases that touch `active:`,
   compare against `persisted_active_name()`, never `active_name()`
   — see "Active vs persisted-active" above.
3. Wire the Typer command in `cli/commands.py`. Mark
   `no_args_is_help=True` if it has required args. Pipe-friendly data
   output via `format_output` + `--format` / `--columns`. Side-effect
   commands log the result to **stderr** with `typer.echo(msg, err=True)`.
   If the command emits a single nested object, narrow `FormatOption`
   to a `Literal[...]` of the formats that actually make sense.
4. Test the use case with a stub satisfying `ProfileRepository` —
   no filesystem, no `Settings`. See `tests/unit/test_create_profile.py`
   for the pattern.
5. If the command writes, also test through `ProfileFileRepository`
   against a real temp `config.yml` so the `mutate_config` path in
   core's helpers is exercised end-to-end.

## See also

- [Root AGENTS.md](../../AGENTS.md) — 4-Layer DDD, Hard Rules,
  cross-cutting helpers index.
- [`untaped-core/AGENTS.md`](../untaped-core/AGENTS.md) — Profile
  resolution internals, Settings schema, "Recipe: add a new setting".
- [`untaped-config/AGENTS.md`](../untaped-config/AGENTS.md) — sibling
  meta-domain operating on the keys *inside* a profile.
- [`docs/configuration.md`](../../docs/configuration.md) — user-facing
  configuration, profiles, secrets, env-var overrides.
