# AGENTS.md — `untaped-core`

Cross-cutting infrastructure: settings/profiles, http+TLS, config file,
output formatting, stdin helpers, errors. Owns the `Settings` schema —
new settings land here. For workspace-wide rules and the cross-cutting
helpers index, see the [root `AGENTS.md`](../../AGENTS.md). For
user-facing config reference, see
[`docs/configuration.md`](../../docs/configuration.md).

## Settings schema (intentional inversion)

`untaped_core.settings` declares one `Settings` model with named
sub-models per domain (`AwxSettings`, `GithubSettings`,
`WorkspaceSettings`). This couples the "shared kit" to every domain by
name — deliberately. One schema in one place is what makes
`config_schema.walk_settings`, `untaped config list`, `redact_secrets`,
and env-var resolution work without a per-domain registration step.
Splitting the schema (one slice per domain plus a federation hook)
would solve a coupling that's tracked but not currently painful, at the
cost of breaking the introspection contract. When a 7th or 8th domain
lands and the coupling starts to feel real, reconsider; until then, new
settings go in an existing sub-model or a new sub-model here.

## Profile resolution (internals)

`~/.untaped/config.yml` is **profile-based**: every configurable value
lives under `profiles.<name>`, never at the top level. Two profile-related
keys live outside that block:

- `active: <name>` — selects which profile is active. May be unset (then
  `default` is used if it exists, otherwise no profile layer applies and
  values come straight from the schema). The `UNTAPED_PROFILE` env var or
  the root `untaped --profile <name>` flag override this for one process.
- `workspace.workspaces` — the workspace registry. **App state**, not
  user-tunable config; stays at the top level and is hoisted back into the
  merged dict by `ProfilesSettingsSource`.

Resolution order, high → low:

```text
env vars (UNTAPED_…)  >  active profile  >  default profile (optional)  >  schema default
```

The `default` profile is **optional**. Schema defaults
(`packages/untaped-core/src/untaped_core/settings.py`) are the implicit
floor for every profile. If `profiles.default` is present, it merges as a
shared overrides layer beneath the active profile.

The root `--profile` flag mutates `os.environ["UNTAPED_PROFILE"]` and
clears `get_settings`'s `lru_cache` immediately, so per-call overrides
take effect even when the cache was already populated.

User-facing semantics (writing, copying, deleting, renaming profiles)
live in [`docs/configuration.md`](../../docs/configuration.md#profiles).

## Config-load error translation

`report_errors` (`untaped_core.cli`) only catches `UntapedError`
subclasses — non-`UntapedError` exceptions surface as raw Python
tracebacks, by design (those represent bugs). The implication: every
site that ingests user data (YAML on disk, env vars, stdin) must
translate library exceptions into a `ConfigError` (or other
`UntapedError` subclass) at the boundary, so a stray typo in
`~/.untaped/config.yml` doesn't look like a crash.

Three sites cover the config-load path today:

- `config_file.read_config_dict` — wraps `yaml.safe_load`, translates
  `yaml.YAMLError` → `ConfigError("could not parse {path}: {exc}")`.
- `settings.ProfilesSettingsSource._load_raw_yaml` — same translation
  on the pydantic-settings source-chain path, so `Settings()`
  construction can't leak a `YAMLError`.
- `settings.get_settings` — wraps `Settings()`, translates
  `pydantic.ValidationError` → `ConfigError("invalid config in {path}:
  {first_validation_error(exc)}")`. Uses the
  `errors.first_validation_error` helper for `loc: msg` formatting.

`untaped-workspace`'s `infrastructure.manifest_repo.ManifestRepository`
follows the same shape (`YAMLError` / `ValidationError` → `ManifestError`)
for the per-workspace `untaped.yml` boundary. When you add a new
user-data ingestion point, follow this pattern: catch the library
exception, raise an `UntapedError` subclass that names the file path
in the message, and chain with `from exc`.

## TLS verification

Defaults to the **OS trust store** via the `truststore` package — corporate
root CAs installed in macOS Keychain, Windows certstore, or the Linux
system trust just work. User overrides (`http.ca_bundle`,
`http.verify_ssl`) are documented in
[`docs/configuration.md`](../../docs/configuration.md).

**Implementation rule:** every domain client passes
`verify=resolve_verify(s.http)` when constructing `HttpClient`. Do not
invent your own.

## Recipe: add a new setting

1. Pick a section: top-level (rare) or one of the existing sub-models
   (`HttpSettings`, `AwxSettings`, `GithubSettings`, `WorkspaceSettings`).
   New bounded context → add a new sub-model to `untaped_core.settings`
   and wire it into `Settings`.
2. Use the right type:
   - `SecretStr | None = None` for any credential.
   - `Path | None = None` for filesystem paths.
   - `bool` / `int` / `str` with sensible defaults otherwise.
3. **Update tests** in
   `packages/untaped-core/tests/unit/test_settings.py` so the new key is
   loaded from YAML (under `profiles.default`) and overridable via env
   var. Top-level keys are no longer honoured — every value lives under a
   profile.
   - **For `SecretStr` fields:** also bump the expected count and add the
     new path tuple in
     `tests/unit/test_config_schema.py::test_secret_field_paths_matches_known_settings_secrets`.
     That test pins the schema's secret inventory so every credential
     stays reachable for redaction by `untaped profile show`; a missed
     update would silently leak the new credential.
4. **Update tests** in
   `packages/untaped-config/tests/unit/test_list_settings.py` to assert
   the new key shows up in `untaped config list`.
5. If the setting is cross-cutting, update the root `AGENTS.md`'s
   "Cross-Cutting helpers" table or this file's TLS / Profiles sections.
6. Verify with `uv run untaped config list` — the new key appears
   automatically since the schema is walked.

## See also

- [Root AGENTS.md](../../AGENTS.md) — 4-Layer DDD, Hard Rules,
  cross-cutting helpers index
- [`docs/configuration.md`](../../docs/configuration.md) — user-facing
  profiles, secrets, TLS, env-var overrides
