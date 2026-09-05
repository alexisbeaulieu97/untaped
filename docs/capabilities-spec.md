# Capabilities Composition Contract (Wave 1.1)

Normative composition specification for the unified `untaped` application.
It replaces the one-process-one-tool model of
`src/untaped/tool.py` (`ToolSpec`), `src/untaped/run.py`
(`build_tool_app` / `run_tool`), `src/untaped/config/use_cases.py`
(`ToolConfigContext.resolve_key`), `src/untaped/skills.py` /
`src/untaped/skills_app.py`, and `docs/plugins.md` with a multi-capability
composition root. `ToolSpec` / `run_tool` remain the operative contract until
the retirement plan in §9 completes.

Conventions: "fatal" means the composition root raises `ConfigError` and the
process exits before dispatch; "quarantine" means the offending external
provider is excluded with a recorded quarantine record while the remaining
application still boots. All field shapes are closed: validators reject
unknown fields.

## 1. ApplicationSpec and CapabilitySpec field shapes

`ApplicationSpec` describes the whole unified CLI. `CapabilitySpec`
describes one composable unit (one former standalone tool). Both are frozen
dataclasses. Neither accepts any field beyond the ones listed here; passing
an unknown keyword is a `TypeError`.

```python
@dataclass(frozen=True)
class ApplicationSpec:
    name: str                    # unified executable name, e.g. "untaped"
    app_factory: Callable[[], App]  # nullary cyclopts app factory for the root app
    config_section: str          # reserved root section owned by the shell itself
    profile_model: type[BaseModel]  # shell-level profile-scoped settings model
    state_model: type[BaseModel] | None  # shell-level tool-managed state, default None
    skills: tuple[SkillAsset, ...]   # shell-level packaged skills, default ()
    doctor_checks: tuple[DoctorCheck, ...]  # shell-level health checks, default ()

@dataclass(frozen=True)
class CapabilitySpec:
    name: str                    # capability name, e.g. "github"
    app_factory: Callable[[], App]  # nullary factory returning the capability sub-app
    config_section: str          # config section this capability owns, e.g. "github"
    profile_model: type[BaseModel]  # profile-scoped (user-tunable) settings model
    state_model: type[BaseModel] | None  # disjoint tool-managed state model, default None
    skills: tuple[SkillAsset, ...]   # packaged agent skills, default ()
    doctor_checks: tuple[DoctorCheck, ...]  # capability health checks, default ()
```

Shared construction rules (carried over from `ToolSpec.__post_init__` in
`src/untaped/tool.py`): `name` and `config_section` must be non-empty after
stripping whitespace; `profile_model` must be a `pydantic.BaseModel`
subclass and `state_model` must be either `None` or a `BaseModel` subclass
(checked by the same `_require_model` predicate); `skills` and
`doctor_checks` are normalized to tuples at construction. `app_factory` must
be a nullary callable: it is invoked with zero arguments at mount time and
must return a `cyclopts.App`. A factory that requires arguments, or returns
a non-`App`, fails validation (§5, "bad factories"). The factory is invoked
lazily at composition time, never at import time, mirroring the lazy
`metadata.version(distribution)` lookup in `build_tool_app`, so importing a
provider never executes app construction.

## 2. CapabilityProvider protocol, entry points, and API versioning

External capabilities are discovered, never imported by path. The discovery
surface is a single module-level contract:

```python
CAPABILITY_API_VERSION: float = 1.0

class CapabilityProvider(Protocol):
    api_requires: tuple[float, float]  # (min_inclusive, max_exclusive) SDK API range
    def __call__(self) -> CapabilitySpec: ...
```

`api_requires` is a plain class/instance attribute on the provider object,
not a parameter: it declares the SDK capability-API range the provider was
built against as `(min_inclusive, max_exclusive)`. The entry point is itself
the provider: the `untaped.capabilities` entry-point group maps a
distribution to `module:attr`, and the resolved `attr` must be a nullary
callable returning a `CapabilitySpec`. Calling it with any argument, or a
target that is not callable at all, is a malformed entry point (§5).
Built-in capabilities skip entry points and construct `CapabilitySpec`
directly in-process.

External-provider return contract: the entry-point callable MUST return a
`CapabilitySpec` instance and nothing else. It MUST NOT register settings,
mount apps, touch `ContextVar`s, or perform I/O as a side effect of being
called; registration happens exactly once inside the composition root after
validation succeeds. A provider whose callable returns any other type, or
raises, violates the return contract and is quarantined with the exception
text recorded.

`untaped.capability_api` is the stable import surface for provider authors.
Its v1 composition set is closed at exactly these eight names —
`ApplicationSpec`, `CapabilitySpec`, `CapabilityProvider`,
`CAPABILITY_API_VERSION`, `SkillAsset`, `DoctorCheck`, `DoctorResult`,
`CapabilityContext`. In addition, `untaped.capability_api` re-exports
exactly these sixteen supported helpers — `ColumnsOption`, `ConfigError`,
`FormatOption`, `StateCollection`, `UiContext`, `UntapedError`,
`app_context`, `create_app`, `echo`, `emit`, `finish`,
`first_validation_error`, `get_config_section`, `raise_usage`,
`read_identifiers`, `report_errors` — the exact helper surface the in-repo
private capabilities import from `untaped.api` today (verified against
`untaped-market` and `untaped-apple-health`; `SkillAsset` is already in the
composition set above, and the retired composition names `ToolSpec`,
`register_tool`, `build_tool_app`, `run_tool` stay excluded). Provider
packages MUST obtain helpers only via `untaped.capability_api`; anything not
re-exported there is out of contract. The `__all__` of
`untaped.capability_api` MUST contain precisely the eight composition names
plus the sixteen supported helpers and nothing else. Adding
a name to `untaped.capability_api` without amending this list is a CI
failure; importing anything from `untaped` outside `capability_api` inside a
provider package is unsupported and may break without a major-version event.

The v1 `untaped.api` surface is exactly the 68 names below. The legacy
composition names `ToolSpec`, `register_tool`, `build_tool_app`, and
`run_tool` — all verified present today in `src/untaped/api.py` lines
64–163 — are explicitly EXCLUDED from the v1 surface and MUST NOT appear in
`__all__`, in any import, or behind any alias once §9 gate 3 passes:

```python
__all__ = [
    "AppContext", "BatchOutcome", "ColumnsOption", "ConfigError",
    "DiffStats", "FileChange", "FileWriteError", "FormatOption",
    "HttpClient", "HttpError", "HttpSettings", "HttpStatusError",
    "HttpTransportError", "OutputFormat", "PipeEnvelope", "ProgressHandle",
    "PromptChoice", "RetryPolicy", "SkillAsset", "StateCollection",
    "StateMap", "ThemeSpec", "UiContext", "UntapedError", "app_context",
    "apply_file_changes", "atomic_write", "batch_apply", "bounded_map",
    "clamp_parallel", "common_kind", "connected_client", "create_app",
    "diff_stats", "echo", "emit", "ensure_config", "existing_directory",
    "existing_file", "finish", "first_validation_error",
    "get_config_section", "get_core_settings", "get_settings",
    "invalidate_settings_cache", "is_envelope_line", "missing_setting_error",
    "mutate_tool_state", "paginate_link", "paginate_offset",
    "paginate_pages", "parse_envelope_line", "parse_json_pairs",
    "parse_kv_pairs", "raise_usage", "read_identifiers", "read_records",
    "read_stdin", "read_stdin_text", "read_structured_file",
    "read_tool_state", "render_rows", "report_errors", "resolve_each",
    "resolve_text_input", "resolve_verify", "ui_context",
    "unified_diff_text",
]
```

## 3. Typed records

All records below are frozen dataclasses with closed fields. `SkillAsset`
keeps its exact current shape from `src/untaped/tool.py` so existing assets
migrate unchanged.

```python
@dataclass(frozen=True)
class SkillAsset:
    name: str        # non-empty after strip; unique per composition (§5)
    source: Path     # directory holding the skill; must exist and be a dir at install-plan time
    description: str # non-empty after strip

@dataclass(frozen=True)
class DoctorCheck:
    id: str                          # stable dotted id, e.g. "github.auth"; unique per composition
    title: str                       # short human-readable title, non-empty after strip
    run: Callable[[CapabilityContext], DoctorResult]  # unary check body

@dataclass(frozen=True)
class DoctorResult:
    id: str        # MUST equal the DoctorCheck.id that produced it
    ok: bool       # True = pass, False = fail
    detail: str    # human-readable one-paragraph outcome, non-empty after strip

@dataclass(frozen=True)
class CapabilityContext:
    capability: str                  # owning capability name (or shell name for shell checks)
    config_section: str              # section the check may read
    profile_fields: frozenset[str]   # profile model field names at compose time
    state_fields: frozenset[str]     # state model field names at compose time
    settings: BaseModel | None       # resolved aggregate settings snapshot, or None pre-resolution
```

`CapabilityContext` is a frozen per-invocation snapshot, modeled on
`AppContext` (`src/untaped/app_context.py`): the check body receives values,
never live registries, and MUST NOT mutate settings, install skills, or
write state. `DoctorResult.id` mismatch (a check returning another check's
id) fails validation.

Registry records (kept by the composition root, not constructed by
providers):

```python
@dataclass(frozen=True)
class RegisteredCapability:
    spec: CapabilitySpec
    provider_ref: ProviderRef  # how this capability arrived
    skills: tuple[SkillAsset, ...]  # resolved asset tuple (== spec.skills normalized)

@dataclass(frozen=True)
class ProviderRef:
    kind: str          # exactly "built-in" or "external"
    distribution: str  # distribution name; built-ins use "untaped"
    entry_point: str   # "module:attr" for external, "" for built-in
    api_requires: tuple[float, float]  # declared range (built-ins declare (1.0, 2.0))

@dataclass(frozen=True)
class QuarantineRecord:
    distribution: str  # offending distribution (or "unknown" when unresolvable)
    entry_point: str   # offending entry-point target, "" when resolution itself failed
    reason: str        # machine-stable code from the validation table (§5), e.g. "duplicate-section"
    detail: str        # human-readable explanation naming the colliding value
```

`reason` MUST be one of the codes in §5. `detail` MUST name the colliding
value (the duplicate name, reserved section, overlapping field, or API
range) so `doctor` can print it without further lookup.

## 4. Invocation-scoped identity, reset, and error/help context

Root command surface: the unified shell owns exactly five root command
groups — `config` (section-scoped settings read/write), `profile` (profile
selection and inspection), `skills` (skill install/list planning), `doctor`
(health checks for the shell and all capabilities), and `capabilities`
(listing and status of composed capabilities, §7.3). No capability may mount
a root-level command under any of these names (reserved roots, §5 row 1).

Today `src/untaped/identity.py` holds a single `ContextVar`
(`untaped_tool_command`) set by `register_tool` and read for command-aware
guidance, while `src/untaped/run.py` scopes `--profile` / `--verbose` /
`--quiet` overrides to one invocation with token-based reset in a `finally`
block. The unified root generalizes both to N capabilities:

- Identity is a `ContextVar[str | None]` holding the *active capability
  name* for the current invocation, defaulting to the shell name when
  dispatch has not yet selected a capability and to `None` when no
  application has composed (SDK used as a library). Command-aware helpers
  (`missing_setting_error`, `ToolConfigContext._state_error` successors,
  help prologues) read this variable so messages name the capability the
  user invoked (e.g. `github.token is not configured (set it via untaped
  config set github.token …)`), never the shell or a sibling capability.
- The variable is set at dispatch time to the selected capability and reset
  to its previous value in a `finally` block, exactly like the
  `applied_tokens` reset loop in `_root_callback`. Nested in-process
  invocations (tests, embedding) therefore restore the outer invocation's
  identity. A composition-root `reset()` clears the identity `ContextVar`,
  the profile/verbose/quiet override tokens, the settings `lru_cache`s, and
  the config registry to the just-composed state; it exists for test
  isolation and is never called implicitly between user invocations.
- Config key resolution keeps the `ToolConfigContext.resolve_key` precedence
  per capability: SDK roots (`Settings.model_fields`: `log_level`, `http`,
  `ui`) win first, then the capability's own state fields raise the
  "managed by" error, then the capability's profile fields resolve under
  `config_section`, and anything else passes through. Help text renders the
  invoked capability's command path (`untaped <name> …`), and `--version`
  resolves the owning distribution lazily per capability, preserving the
  `build_tool_app` rule that only `--version` touches package metadata.
- Root config resolution is direct, not delegated per tool: a fully
  qualified `section.key` selects its schema by `section` — shell-owned
  sections resolve against the shell models, any other section against the
  owning capability's `profile_model`. A `config set` targeting a
  state-managed field is rejected per that section's `state_model` with the
  "managed by" error and never writes. Once the section has selected the
  capability, the per-capability precedence above applies unchanged.
- Legacy per-tool management commands are classified exactly once.
  Replaced (prefix change — the capability becomes a section/name argument
  instead of the executable): `<tool> config …` → `untaped config …`,
  `<tool> profile …` → `untaped profile …`, `<tool> skills …` →
  `untaped skills …`, `<tool> doctor …` → `untaped doctor …`. Removed with
  mechanism: per-tool `uv tool` install environments and standalone per-tool
  `--version` (mechanism: single-distribution packaging change, §8 uninstall
  notices, and the `docs/plugins.md` rewrite gated by §9).
- Failure isolation (normative acceptance case): invalid Jira settings —
  unparseable, missing, or schema-rejected Jira profile values — MUST NOT
  block `workspace`, `--help`, `capabilities` listing, or config repair
  (`config set` / `ensure_config` paths that fix the breakage). `doctor`
  MUST still report the Jira failure as a failed row. The mechanism (lazy
  per-capability settings resolution, dispatch-time error boundaries, or
  equivalent) is the implementer's choice; the case itself is normative and
  blocks retirement until covered by test.

## 5. Provider validation and transactional quarantine

Composition runs in four explicit phases per provider, in phase order.
Deterministic ordering: built-in capabilities compose first, in
spec-declaration order; external providers follow sorted by `(distribution,
entry-point name)`. Built-ins take precedence: an external candidate that
collides with an already-composed built-in fails the colliding row (and is
quarantined) rather than displacing it.

- Phase A — discovery and API pre-checks: enumerate entry-point targets in
  the order above, run the row-13 metadata gates (entry-point group and
  `Requires-Dist` admission) BEFORE resolving any target, then resolve each
  surviving target to a provider object and check its `api_requires` range
  (row 10). Unresolvable targets and non-callables fail
  the resolution part of row 11 here.
- Phase B — resolve provider to obtain spec: call the provider nullary. A
  provider that raises, requires arguments, or returns a non-`CapabilitySpec`
  fails row 11 with the exception text in `detail`. Resolution is
  side-effect free per the §2 return contract.
- Phase C — validate declarations and stage app construction: rows 1–8
  (declaration shape only), then row 12, which invokes `app_factory` with
  zero arguments to stage construction. Doctor-check BODIES never run in
  this phase.
- Phase D — commit registration: only after every applicable row passes, the
  provider registers its settings sections, mounts its app, registers skill assets for discovery and install planning,
  and contributes its doctor checks. Filesystem installation belongs exclusively to `untaped skills install`.

Outcome "built-in fatal" means a violating built-in raises `ConfigError`
and the process exits before dispatch: built-ins ship with the SDK, so a
built-in violation is an SDK bug, never a runtime condition. Outcome
"external quarantine" means the provider is excluded, a `QuarantineRecord`
is appended, and composition continues with the remaining providers.
Transactional-quarantine guarantee: validation of each external provider is
side-effect free until that provider fully passes; a provider that fails any
row registers no settings sections, mounts no apps, registers no skill assets,
contributes no doctor checks, and leaves previously composed providers
untouched. A failed provider can therefore never half-register. Quarantine
records are surfaced by `doctor` and by a one-line stderr warning per
quarantined distribution at startup.

Doctor-check execution lives OUTSIDE composition: check bodies run only at
doctor-execution time (the root `doctor` command, row 9). A body that
raises, returns a non-`DoctorResult`, or returns a `DoctorResult` whose `id`
differs from its `DoctorCheck.id` produces a failed diagnostic row carrying
reason `doctor-check-failed`, while all other checks still run. Quarantine
never happens post-execution: execution-time failures mount, register, or
unregister nothing, so the never-mounted/never-registered guarantee holds
for every provider that passed Phase D.

| # | Phase | Check | Rule | reason code | Built-in outcome | External outcome |
|---|-------|-------|------|-------------|------------------|------------------|
| 1 | C | Reserved roots | `name` and `config_section` MUST NOT be in `Settings.model_fields` (`log_level`, `http`, `ui`) and MUST NOT be `profiles`, `active`, `config`, `profile`, `skills`, `doctor`, or `capabilities` — the full reserved root command surface (§4). Extends `_reject_reserved_section` (`src/untaped/settings.py`), which today covers only model fields, to the layout/command roots the unified shell owns. | `reserved-root` | fatal | quarantine |
| 2 | C | Duplicate capability names | `name` MUST be unique across the composition including the shell name. Comparison is exact and case-sensitive. | `duplicate-name` | fatal | quarantine |
| 3 | C | Duplicate config sections | `config_section` MUST be unique across the composition including `ApplicationSpec.config_section`. | `duplicate-section` | fatal | quarantine |
| 4 | C | Profile/state overlap | `profile_model` and `state_model` field sets MUST be disjoint (same `validate_disjoint_settings_sections` predicate as `src/untaped/settings.py`); overlap raises naming the sorted overlapping fields. | `profile-state-overlap` | fatal | quarantine |
| 5 | C | Cross-capability state shadowing | No capability's `state_model` field set may intersect another registered section's profile field set under the same section name; state fields are never exposed to `config set`, per `resolve_key`'s state-first rejection. | `state-shadow` | fatal | quarantine |
| 6 | C | Skill name collisions | Every `SkillAsset.name` MUST be unique across the whole composition (shell plus all capabilities), extending the per-`ToolSpec` duplicate check in `src/untaped/tool.py` to the union. | `duplicate-skill` | fatal | quarantine |
| 7 | C | Skill asset shape | Each asset's `name`/`description` MUST be non-empty after strip (existing `SkillAsset.__post_init__` rule, unchanged). | `bad-skill-asset` | fatal | quarantine |
| 8 | C | DoctorCheck ID collisions | Every `DoctorCheck.id` MUST be unique across the composition; empty ids and empty titles are rejected. Bodies are not executed. | `duplicate-doctor-check` | fatal | quarantine |
| 9 | — (execution) | Doctor execution failures | Check BODIES run at doctor-execution time, never at compose time. A body that raises, returns a non-`DoctorResult`, or returns an id-mismatched result yields a failed diagnostic row with this reason while other checks continue; quarantine never happens post-execution. | `doctor-check-failed` | fails self-test | failed diagnostic row only |
| 10 | A | API range | The provider's `api_requires` `(lo, hi)` MUST satisfy `lo <= CAPABILITY_API_VERSION < hi` with `lo < hi` and both finite numbers; a missing, non-pair, non-numeric, or inverted range fails this row, not row 11. | `api-range` | fatal | quarantine |
| 11 | A/B | Malformed entry points | The entry-point target MUST resolve to a nullary callable returning a `CapabilitySpec`. Unresolvable targets, non-callables (Phase A), callables requiring arguments, callables returning a non-`CapabilitySpec`, and callables that raise (Phase B) all fail here with the exception text in `detail`. | `malformed-entry-point` | n/a (no entry point) | quarantine |
| 12 | C | Bad factories | A resolved `app_factory` that requires arguments or returns a non-`cyclopts.App` fails here (entry-point callables that never return a spec fail row 11 instead). | `bad-app-factory` | fatal | quarantine |
| 13 | A | Distribution metadata | Metadata gates run in Phase A before any import: built-ins verify the §7.1 invariants (`distribution == "untaped"`, `entry_point == ""`, version == product version — a mismatch is an SDK bug); externals run the §7.2 checks (entry-point group and naming, `Requires-Dist` admission). | `bad-metadata` | fatal | quarantine |

Phases run in order A→D per provider and stop at the first failure for that
provider; section registration for the provider happens only in Phase D
after every applicable row passes. Built-in candidates run rows 1–8, 10, 12,
and 13 (row 11 does not apply: there is no entry point to resolve; row 9 is
diagnostic-only and a failing built-in body fails the self-test run, never
composition). This table is the single home of every `reason` code: §3
`QuarantineRecord` reasons and §7 compose-mode records MUST use a code from
this table and no other value.

## 6. Dependency-policy artifacts and CI validators

Two independent checks run in CI on every pull request.

(a) Distribution runtime dependencies. Each in-repo capability's runtime
dependencies (`pyproject.toml` `dependencies`) MUST be recorded in
`docs/runtime-deps.toml`, one entry per direct dependency naming the owning
capability, a rationale, and the exact requirement string:

```toml
version = 1
[[dep]]
name = "httpx"
owner = "github"
reason = "paginated REST calls from github commands"
requirement = "httpx>=0.27,<0.29"
```

`name` is the depended-on distribution, `owner` the owning capability,
`reason` a non-empty human justification, and `requirement` the exact
normalized requirement string for that dependency as declared in
`pyproject.toml` (normalized name plus version specifier, extras, and
markers). CI (`tools/validate_deps.py`) fails the build on any unrecorded
direct-dependency addition, any mismatch between a record's `requirement`
and the normalized `pyproject.toml` requirement for the same dependency —
widened or narrowed bounds, changed pins, added or removed extras, renames,
or any other difference, with the differing value named — or any record
matching no current dependency (stale records must be removed, not left to
rot). Exact-normalized-match subsumes broadening detection: any specifier
change without an updated record fails.

(b) Internal cross-capability imports. A capability MUST NOT import another
capability's implementation modules, matched against explicit capability
module prefixes of the form `untaped.capabilities.<name>.*`. The narrow
exception is an explicit allow-list carried in-repo as
`docs/dependency-policy.toml`:

```toml
version = 1
[[allow]]
importer = "untaped.capabilities.ansible.*"
imported = "untaped.capabilities.github.pagination"
reason = "shared paginator; no settings access"
```

`importer` and `imported` are dotted module prefixes; `reason` is a
non-empty human justification naming what is shared and confirming no
settings/registry access crosses the boundary. The ansible→github exception
is scoped to the named API module above — blanket imports of another
capability's implementation package remain forbidden. Unknown top-level
keys, a `version` other than `1`, an `allow` entry missing any of the three
keys, an empty `reason`, a prefix matching no capability module, or a
duplicate `(importer, imported)` pair makes the artifact itself invalid and
fails the validator closed.

The CI validator (`tools/validate_deps.py`, run on every pull request)
statically scans each capability package's imports and fails the build when
(1) either TOML artifact is invalid per above, (2) any cross-capability
import lacks a matching `allow` entry, or (3) any `allow` entry matches no
observed import (stale entries must be removed, not left to rot). The
validator reads only source; it never imports capability packages. Its
failure message names the file, the offending import, and the missing
`(importer, imported)` pair.

## 7. Metadata validator and capabilities listing

### 7.1 Built-in metadata

Built-in capabilities declare no entry points: their `CapabilitySpec`
objects are constructed directly in-process, their `ProviderRef` uses
`kind = "built-in"`, `distribution = "untaped"`, and `entry_point = ""`
with `api_requires = (1.0, 2.0)`, and their version is always the unified
product version — never a per-capability version.

### 7.2 External distribution metadata

The metadata validator (`tools/validate_metadata.py`, run on every pull
request and at compose time for externals in lenient mode) checks each
external capability's packaging metadata without importing it: the
distribution MUST declare its capabilities in the `untaped.capabilities`
entry-point group — one distribution MAY expose multiple entry points, one
per capability it provides; each entry-point name MUST equal its capability
`name`; the distribution name MUST equal the provider's
`ProviderRef.distribution`; `Requires-Dist` on `untaped` MUST admit the
running SDK version. (Former loader-mapping closed-shape check removed as
YAGNI: no producer ever populated `loader_fields`, so the check was
unreachable on the real discovery path.) CI mode fails the build on the
first violation with the distribution, the violated rule, and the offending
value. Compose mode never raises for externals: it records a
`QuarantineRecord` with `reason = "bad-metadata"` (defined in §5, row 13)
and the validator message as `detail`. All reason codes — quarantine and
diagnostic — are defined in the single §5 table; no other `reason` value is
valid anywhere.

### 7.3 Capabilities-listing record

The root `capabilities` command reports one record per candidate provider:

```python
@dataclass(frozen=True)
class CapabilityListing:
    name: str                          # capability name (entry-point name when quarantined pre-spec)
    status: str                        # exactly "ready" or "quarantined"
    version: str                       # product version for built-ins; distribution version for externals
    api_requires: tuple[float, float]  # declared SDK range; (0.0, 0.0) when unresolvable
    provider_ref: ProviderRef | None   # None only when the provider never resolved
    quarantine: QuarantineRecord | None  # set iff status is "quarantined"
```

`status` derives from the composition outcome; a `quarantined` listing MUST
still carry its `QuarantineRecord` with the §5 reason code so `doctor` can
print the cause without further lookup.

## 8. Legacy-install ownership detection

Independently installed `uv tool` copies of the former standalone CLIs
(`untaped-github`, `untaped-jira`, …) may still shadow the unified `untaped`
on `PATH`. On startup the shell resolves, for each built-in capability name,
which executable the shell would invoke and compares it against its own
managed installs:

- Detection: for each known legacy command, run `shutil.which(command)`;
  when a hit resolves outside the current environment's managed prefix
  while the unified shell provides the same capability, record an
  ownership notice naming the command, the shadowing path, and the
  unified replacement (`untaped <name>`).
- Behavior: detection is advisory only. It never quarantines, never
  reorders `PATH`, never uninstalls, and never fails startup or CI. Notices
  render once per process as stderr warnings and appear in full under
  `doctor` as non-failing informational rows.
- Precedence: when both exist, in-process dispatch always uses the composed
  capability; the notice tells the user how to remove the shadow
  (`uv tool uninstall <command>`) but takes no action itself.

## 9. ToolSpec/run_tool retirement plan

`ToolSpec`, `register_tool`, `build_tool_app`, and `run_tool` stay operative
until all three retirement gates below pass; the spec in this file is
additive until then and changes no runtime behavior on its own.

1. Migration: each suite tool gains a `CapabilitySpec` constructor
   (`SPEC` object plus a nullary `build_app()` factory) alongside its
   existing `ToolSpec` + `main()`; the unified shell composes the new
   constructors while the standalone entry points keep calling `run_tool`.
   `SkillAsset` moves unchanged (identical field shape, §3). `resolve_key`
   precedence moves per capability into the shell dispatcher (§4) with no
   precedence change. Migration is complete when every suite tool builds
   both ways and the unified shell's help/config/skills/doctor output
   matches the standalone tools under the parity rules of §10.
2. Test retirement: port each `tests/unit/test_run_tool.py` case to the
   composition root (mount idempotency, lazy version lookup, root-option
   reset, command-aware errors), then delete the `ToolSpec`-only tests.
   No test may assert on `ToolSpec` or `run_tool` after this gate except
   the negative CI proof below.
3. Negative CI proof: a CI job (`tools/no_legacy_composition.py`) fails the
   build when `src/untaped/tool.py` or `src/untaped/run.py` still defines
   `ToolSpec`/`register_tool`/`build_tool_app`/`run_tool`, when any
   non-test source imports them, when `untaped.api.__all__` (or the package
   root re-export) still exports any of the four names, or when any alias
   re-exports them under another name (`as` imports, wrapper assignments,
   or equivalent). Only after this job passes may the two
   modules be deleted and `docs/plugins.md` rewritten to the capability
   authoring guide. `docs/plugins.md` remains the standalone-tool guide
   until that rewrite lands.

## 10. Parity classification and semantic-golden normalization

Every observable behavior of the retired standalone tools is classified
exactly once before removal:

- preserve: behavior the unified shell MUST reproduce byte-for-byte
  (config file layout and precedence, pipe NDJSON envelope v1, skill
  install planning/rollback, error exit codes, `--format` rendering).
  A preserve item with no covering golden test blocks retirement.
- intentionally-replace: behavior that changes on purpose (program name in
  help/usage becomes `untaped <name>` instead of `untaped-<name>`;
  `--version` reports the unified distribution version; top-level
  `--help` lists all composed capabilities). Each item MUST cite the
  decision record authorizing the change; an item without a citation is
  reclassified preserve.
- remove: behavior that disappears entirely (per-tool `uv tool` install
  environments, standalone `--version` per tool, the retired hub shims).
  Each item MUST name its removal mechanism (docs rewrite, packaging
  change, uninstall notice via §8); an item without a mechanism blocks
  retirement.

Semantic-golden comparison normalizes exactly the following volatile
surface before diffing unified output against standalone goldens, and
nothing else: timestamps (ISO-8601 datetimes and epoch values replaced
with `<timestamp>`), temp paths (per-run staging/backup directories and
`$TMPDIR`-rooted paths replaced with `<tmp>`), IDs (UUIDs, backup hex
suffixes, and generated skill-install markers replaced with `<id>`),
styling (ANSI color codes stripped and trailing whitespace collapsed),
ordering (row lists sorted by the same key the command documents, e.g.
skill rows by name per `skill_rows` in `src/untaped/skills.py`). Any diff
remaining after this normalization is a real parity failure, not noise;
widening the normalization list to make a failing golden pass is forbidden
without a new decision record.
