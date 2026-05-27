# AGENTS.md — `untaped-core`

Cross-cutting infrastructure: settings/profiles, http+TLS, config file,
output formatting, stdin helpers (`read_stdin`, `read_identifiers`, and
the companion `resolve_each` per-id loop), errors. Owns the `Settings` schema —
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

A commented sketch of the future federation hook (a `DomainSettings`
Protocol plus an `importlib.metadata` entry-point loader) lives at the
foot of `untaped_core/settings.py`. It's documentation, not code —
turn prose into runnable Python when the domain count actually
warrants the indirection.

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

`validate_settings_isolated(settings_cls, data)` (also in
`untaped_core.settings`) handles the inverse problem: validating a
candidate dict against the schema *without* re-running the source
chain. Read-modify-write flows that compose a new YAML state from
disk + edits need this — otherwise the on-disk-but-not-yet-flushed
file source masks bugs in the candidate (an `unset` that would leave
the file invalid validates "successfully" because the file source
still holds the value being removed). `untaped-config`'s
`set`/`unset` write path is the canonical caller.

## TLS verification

Defaults to the **OS trust store** via the `truststore` package — corporate
root CAs installed in macOS Keychain, Windows certstore, or the Linux
system trust just work. User overrides (`http.ca_bundle`,
`http.verify_ssl`) are documented in
[`docs/configuration.md`](../../docs/configuration.md).

**Implementation rule:** every domain client passes
`verify=resolve_verify(s.http)` when constructing `HttpClient`. Do not
invent your own.

## `--format raw` default-column contract

`_format_raw` (`output.py`) has two paths:

1. `--columns` supplied → emit those columns, tab-separated, one row
   per line.
2. `--columns` omitted → emit `next(iter(rows[0]))` for every row
   (one value per line — the identifier).

The fallback in (2) makes the **first key of every row** load-bearing
for shell pipelines. **List** use cases promise that the first key is
the row's identifier — the value a downstream `xargs` would feed back
into another `untaped` / `gh` / `awx-cli` command. Reordering keys in
a row dict or in a `model_dump()` source is a breaking change for
pipeline callers; treat it as part of each list command's public
contract.

Scope: this contract covers **list-style data emission** (`list`,
`get`, `status`, `search`, `jobs list`, `test list`, …). Two
categories are exempt:

- **Side-effect summary rows** — `awx apply`'s `outcome_rows`
  (`packages/untaped-awx/src/untaped_awx/cli/format.py`) keys by
  `(kind, name)`, not a single pipeable identifier; `--format raw`
  on `apply` is not a documented pipe pattern.
- **Single-row payloads** — `awx ping` emits one `PingStatus` row
  whose first field is `version` (a health discriminator, not an
  identifier). The pipeline use case "feed back into the next
  command" doesn't apply to one-shot health snapshots.

Audit of in-scope row sources (pinned by
`tests/unit/test_format_raw_first_key.py` at the workspace root):

| Row source                                  | First key   |
| ------------------------------------------- | ----------- |
| workspace `list_command` (hand dict)        | `name`      |
| `SyncOutcome` / `StatusEntry` / `ForeachOutcome` | `workspace` |
| profile `list_command` (hand dict)          | `name`      |
| config `list_command` (`_entry_to_row`)     | `key`       |
| `awx test list` table/raw branch (hand dict, `_test_case_row`)    | `suite`     |
| `awx test list` json/yaml branch (hand dict, `_test_suite_row`)   | `suite`     |
| `CaseResult` (`awx test run`, every `--format`) | `suite`   |
| `awx <kind> delete` (hand dict, `_delete_row`) | `id`     |
| `Job` / `JobEvent`                          | `id` / `counter` |
| `WorkflowNode` (`workflow-templates nodes`) | `id`        |
| `AwxResourceSpec.list_columns[0]` (every spec) | `id`     |
| AWX REST record (raw dict from server) — used by `awx <kind> get` when no `--columns` given; server-controlled, not test-pinned | `id` |
| `GithubUser` (`whoami`)                     | `login`     |
| `RepoResult` / `IssueResult` / `UserResult` | `id`        |
| `CodeResult`                                | `name`      |

Two notes:

- **GitHub REST shape.** `RepoResult`/`IssueResult`/`UserResult` lead
  with numeric `id` (matches the upstream REST shape); pipelines
  typically want `--columns full_name` (repos) or `--columns login`
  (users) instead. `CodeResult` has no `id` at all and leads with
  `name` (the filename) — the natural pipeable handle for code
  search.
- **`make_resource_app`-built `list` commands** always pass
  `spec.list_columns` when the user omits `--columns`
  (`cli/_list.py`), so `_format_raw`'s first-key
  fallback never fires for them; the table row above guards
  `awx <kind> get` (which leaves cols `None` for non-table formats
  via `default_get_columns`) and any future caller that swaps the
  default. Similarly, `workflow-templates nodes` always passes
  explicit columns.

When you add a new list command or row-source model, keep an
identifier in position 0 and add the row to the table.

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
