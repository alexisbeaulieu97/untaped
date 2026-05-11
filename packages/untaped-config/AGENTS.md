# AGENTS.md — `untaped-config`

Meta-domain that operates on **profile contents** — the keys *inside* a
profile in `~/.untaped/config.yml`. Sister meta-domain
[`untaped-profile`](../untaped-profile/) owns the inventory itself
(create/use/show/delete/rename profiles); this package owns the
get/set/list of values within whichever profile is targeted. For
workspace-wide rules and the cross-cutting helpers index, see the
[root `AGENTS.md`](../../AGENTS.md). For user-facing reference, see
[`docs/configuration.md`](../../docs/configuration.md).

## Schema-driven introspection

Three commands ship: `list`, `set`, `unset`. All three are
schema-driven — they walk `untaped_core.config_schema` rather than
hard-coding key names:

- `list_settings.py` exposes two use cases: `ListSettings` renders the
  *effective* view and is the default for `untaped config list`. Each
  row's `Source` resolves to one of `env` / `profile:<name>` / `default`
  / `unset` (the last when no value is set and the descriptor has no
  default). `ListAllProfilesSettings` is the `--all-profiles` view,
  emitting one row per `(profile, key)` pair where the profile sets the
  leaf (schema defaults excluded).
- `set_setting.py` / `unset_setting.py` resolve the user-supplied dotted
  key via `SettingsFileRepository.descriptor()`, which calls
  `find_descriptor` and raises `ConfigError` with the full set of valid
  keys if it returns `None`.

Adding a new setting is automatic from this side — see
[`untaped-core/AGENTS.md` "Recipe: add a new setting"](../untaped-core/AGENTS.md#recipe-add-a-new-setting).
The new key shows up in `untaped config list` without any wiring change
in this package.

## Write path: atomic mutate, with validation on `set`

Both `set_value` and `unset_value` in
`infrastructure/settings_repo.py` go through
`untaped_core.config_file.mutate_config` — a file-locked atomic
read-modify-write helper. Don't call `read_config_dict` /
`write_config_dict` directly from this package; concurrent CLIs would
clobber each other otherwise.

Validation is asymmetric. `set_value`'s `_apply` callback mutates the
in-memory dict via `set_at_path`, then calls `_merge_for_validation`
(which runs `resolve_profiles` with `active_override=target` and then
`splice_workspace_registry` to hoist the top-level `workspace.workspaces`
registry back into the merged dict, returning the effective dict),
then runs `Settings.model_validate(merged)` directly in the callback.
If validation fails, `_apply` raises `ConfigError` and `mutate_config`
never flushes the new YAML to disk. Any new setting that depends on the
workspace registry being present at validation time inherits this for
free. `unset_value`'s `_apply` callback only calls `unset_at_path`
inside the lock — there is no schema-validation pass on remove. A
removal that leaves the merged dict in a state pydantic would reject is
detected at next-load (`get_settings`), not at unset time.

The `active_override=target` argument is load-bearing when writing to a
non-active profile. Validating against the live profile's view would
let an invalid value silently land on disk, only blowing up the day
the target profile gets activated. Always validate the *target* view.

## Profile target resolution

`_resolve_target_profile` in `infrastructure/settings_repo.py` is the
contract:

- An explicit `--profile` must already exist in the file. Exception:
  `--profile default` is auto-bootstrapped if missing (it's the
  implicit floor; treating it as "must exist" would be hostile).
- No `--profile` → fall back to
  `effective_active_profile_name(data)`. If the recorded active
  profile doesn't exist either, raise `ConfigError` pointing the user
  at `untaped profile use` / `untaped profile create`.

## Redaction

`domain/formatting.py:display_value` is the redaction site for
`untaped config list`. It masks any value where `descriptor.is_secret`
is true, plus any `SecretStr` instance, with `"***"`. The
`--show-secrets` flag flips `reveal_secrets=True` and unmasks them.
`untaped_core.redact_secrets` (used by `untaped profile show` in
`untaped-profile`) is the dict-walking cousin — same intent, different
shape.

## Layering

Standard 4-layer DDD per root AGENTS.md "Architecture: 4-Layer DDD".
Two package-specific notes:

- **One consolidated port.** `application/ports.py` declares a single
  `SettingsRepository` Protocol with every method the use cases need —
  the seven read-side methods that power `ListSettings` /
  `ListAllProfilesSettings`, plus `set_value` and `unset_value` for
  `SetSetting` / `UnsetSetting`. Each use case takes the wide port via
  constructor injection and only calls the methods it needs; structural
  typing doesn't penalise the unused surface.
- **One concrete adapter.** `SettingsFileRepository` satisfies the port
  structurally (no inheritance). It owns coercion (`_coerce_scalar` via
  `yaml.safe_load`), validation, profile target resolution, and the
  `mutate_config` calls.

## Recipe: add a new config sub-command

1. Add a method to `SettingsFileRepository` if the new command needs an
   external operation the repo doesn't already expose (hypothetically,
   an `export_value` that emits dotted-form output).
2. Add a use case in `application/`. If it needs a method
   `SettingsRepository` doesn't expose yet, add the method to the
   Protocol in `application/ports.py` and to the adapter; the use case
   then takes `SettingsRepository` via constructor injection and calls
   only what it needs.
3. Wire the Typer command in `cli/commands.py`. Mark
   `no_args_is_help=True` if it has required args. Pipe-friendly data
   output via `format_output` + `--format` / `--columns`. Side-effect
   commands log the result to **stderr** with `typer.echo(msg, err=True)`.
4. If the command takes a CLI-supplied scalar, parse it through
   `_coerce_scalar` so `"true"` / `"42"` / `"null"` become the obvious
   types — same contract as `set`.
5. Test the use case with a stub satisfying the new Protocol — same
   pattern as `tests/unit/test_list_settings.py`. No filesystem, no
   `Settings`.
6. If the command writes, test through `SettingsFileRepository` against
   a real temp `config.yml` so the `mutate_config` + validation path
   is exercised end-to-end.

## See also

- [Root AGENTS.md](../../AGENTS.md) — 4-Layer DDD, Hard Rules,
  cross-cutting helpers index.
- [`untaped-core/AGENTS.md`](../untaped-core/AGENTS.md) — Settings
  schema, profile resolution, "Recipe: add a new setting".
- [`untaped-profile/`](../untaped-profile/) — sibling meta-domain
  managing the profile inventory.
- [`docs/configuration.md`](../../docs/configuration.md) — user-facing
  configuration, profiles, secrets, env-var overrides.
