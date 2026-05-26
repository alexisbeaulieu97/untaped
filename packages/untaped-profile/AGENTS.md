# AGENTS.md — `untaped-profile`

Meta-domain that owns the **profile inventory** in
`~/.untaped/config.yml` — which profiles exist, which is active, how
to create / use / show / delete / rename them. Sister meta-domain
[`untaped-config`](../untaped-config/AGENTS.md) operates on the keys
*inside* whichever profile is targeted; this package operates on the
top-level `profiles.<…>` blocks and the `active:` pointer themselves.

## Inventory surface

Commands map one-to-one onto use cases in `application/`: `list`,
`show`, `use`, `current`, `create`, `delete`, `rename`. Each use case
talks to a single concrete adapter (`ProfileFileRepository` in
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
user's persisted active pointer behind their back.** Today,
`DeleteProfile` is the only use case that *consults*
`persisted_active_name()` directly (to refuse deletion of the
persisted active) — otherwise running
`untaped --profile staging profile delete staging` while `production`
was the persisted active would refuse the delete based on a transient
override, which is hostile. `RenameProfile` doesn't consult either
accessor; it delegates `active:` consistency to
`untaped_core.config_file.rename_profile` (which updates the pointer
in the same `mutate_config` op when the renamed profile was active).
Future mutating use cases that *consult* the active pointer should
use `persisted_active_name()`.

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

The root `--profile <name>` flag flows through `UNTAPED_PROFILE`
(see `untaped-core/AGENTS.md` profile resolution), so a per-call
override is reported as `source=env` — `current` doesn't need a
separate flag-detection path.

## Mutation invariants

Rules spread across the mutating use cases. A new mutating use case
should honour the same set:

- `RenameProfile` rejects empty new names, rejects renaming
  `default`, and rejects `default` as the rename target (it's the
  implicit floor; renaming it would break the fallback layer). When
  the renamed profile is the *persisted* active one, `active:` is
  updated in the same `mutate_config` op via
  `untaped_core.config_file.rename_profile` — so the pointer never
  points at a missing profile mid-rename.
- `DeleteProfile` refuses to delete the persisted active profile
  (would orphan `active:`). `default` is **not** special-cased — when
  it's not active, deleting `default` just clears any shared
  overrides and values fall through to schema defaults.
- `CreateProfile` rejects empty names and already-existing names
  (returning the known-profiles list in the error). Deep-copies on
  `--copy-from` so later edits to the source profile don't bleed
  into the new one.

## Redaction

`untaped profile show` redacts secrets at the **CLI layer**
(`cli/commands.py`) using
`redact_secrets(profile.data, secret_field_paths(Settings))` from
`untaped_core` — the dict-walking variant. Both `--format yaml` and
`--format json` redact; `--show-secrets` reveals. Note that
`profile.data` here is the **resolved view by default** (default ⤥
named) and only the verbatim block under `--raw` — `ShowProfile`
overloads `Profile.data` based on the `--raw` flag in
`show_profile.py`, so the `Profile` model's docstring ("verbatim
block, no fallback merge") is true for `ListProfiles` but not for
the default (`--raw=False`) path of `show`. For the row-rendering
cousin used by `untaped config list`, see
[`untaped-config/AGENTS.md` "Redaction"](../untaped-config/AGENTS.md#redaction).

## Layering

Standard 4-layer DDD per root AGENTS.md "Architecture: 4-Layer DDD".
Three package-specific notes:

- **Reader / writer-axis split.** `application/ports.py` declares four
  Protocols layered by two axes — read vs. write, and "writes profile
  data" vs. "writes the active-profile pointer":
  - `ProfileReader` — six read-side methods used by `ListProfiles` /
    `ShowProfile` / `CurrentProfile`.
  - `ProfileWriter(ProfileReader, Protocol)` — adds `write` / `delete`
    / `rename` for `CreateProfile` / `DeleteProfile` / `RenameProfile`.
  - `ActiveProfileWriter(ProfileReader, Protocol)` — adds `set_active`
    for `UseProfile`. Parallel sibling to `ProfileWriter` because
    `UseProfile`'s actual surface is `set_active` only; a linear
    `ProfileReader ⊂ ProfileWriter ⊂ ProfileRepository` chain would
    over-grant `write` / `delete` / `rename` to it.
  - `ProfileRepository(ProfileWriter, ActiveProfileWriter, Protocol)` —
    the widest variant; concrete adapters satisfy it structurally.
  Pick the narrowest Protocol at each use case's constructor —
  read-only use cases can't accidentally grow a mutation call past
  mypy, and writer-axis writers can't accidentally rewrite the
  active-profile pointer.
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

Generic Typer wiring (no_args_is_help, `--format`/`--columns`,
stderr-only side-effect logging, stub-driven use case tests) follows
the root AGENTS.md "Recipe: Add a new command to an existing domain".
Package-specific notes:

1. If the command needs an external operation the repo doesn't already
   expose, add the method to the matching Protocol in
   `application/ports.py` (read-only → `ProfileReader`; data writes →
   `ProfileWriter`; `active:` pointer → `ActiveProfileWriter`) *and*
   to `ProfileFileRepository`. Constructor-inject via the narrowest
   port the use case actually needs.
2. For mutating use cases that touch `active:`, compare against
   `persisted_active_name()`, never `active_name()` — see "Active vs
   persisted-active" above.
3. If the command emits a single nested object, narrow `FormatOption`
   to a `Literal[...]` of the formats that actually make sense (see
   `show` for the pattern).
4. If the command writes, also test through `ProfileFileRepository`
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
