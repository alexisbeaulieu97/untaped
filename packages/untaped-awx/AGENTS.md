# AGENTS.md — `untaped-awx`

Internals of the AWX/AAP bounded context for AI agents and contributors.
For user-facing setup and command reference, see
[`docs/awx.md`](../../docs/awx.md). For workspace-wide rules (4-layer DDD,
Hard Rules, recipes), see the [root `AGENTS.md`](../../AGENTS.md).

## `AwxConfig` — package-local config

`AwxConfig` (`infrastructure/config.py`) is the package-local config struct
(`base_url`, `token`, `api_prefix`, `default_organization`, `page_size`).
Only `cli/` modules read `untaped_core.Settings` — today
`cli/_context.awx_config_from_settings` (the composition root) and
`cli/commands.py:ping_command`. New commands follow the same pattern.
`application/`, `infrastructure/`, and `domain/` depend on `AwxConfig` so
`untaped-awx` is extractable as a standalone library.

## AAP/AWX compatibility

AAP and upstream AWX differ only in URL prefix: `awx.api_prefix` defaults
to `/api/controller/v2/` (AAP); upstream-AWX users set `/api/v2/`. Every
URL flows through `AwxClient._url(path)` so the prefix is honoured
uniformly.

## Resource framework

The AWX surface (5+ kinds × list/get/save/apply + launch) is too uniform
to hand-write per-kind without copy-paste, so we drive it from declarative
specs.

### `ResourceSpec` and `AwxResourceSpec`

`ResourceSpec` (`domain/spec.py`) declares each kind's *domain* contract:
`kind`, `identity_keys`, `canonical_fields`, `read_only_fields`, `fk_refs`,
`secret_paths`, `actions`, `apply_strategy`, `fidelity`, `fidelity_note`.
`apply_strategy` is a behaviour selector (a string the `StrategyResolver`
maps to a concrete `ApplyStrategy`); it lives in domain because the choice
of strategy is per-kind semantics, not transport. Application use cases
depend only on this view.

`AwxResourceSpec` (`infrastructure/spec.py`) extends it with the AWX REST
+ CLI wiring: `cli_name`, `api_path`, `list_columns`, `commands`. Per-kind
specs live in `infrastructure/specs/{job_template, workflow, project,
credential, schedule, host, group, _support}.py` and are aggregated into
`ALL_SPECS`. **Spec fields stay honest with the CLI:** a knob only lives in
the spec if the factory actually wires it. The launch parser
(`cli/resource_commands._add_launch`) enforces this structurally — each
flag whose payload field isn't in the kind's `ActionSpec.accepts` is
passed `Option(hidden=True)` so it's omitted from `--help` while still
being parseable (the runtime guard `_reject_unsupported_launch_flags`
catches a user who passes a hidden flag anyway).

### Typed boundary

`domain/payloads.py`. `ResourceClient` reads return `ServerRecord`
(Pydantic, `extra="allow"`, dict-style access via `__getitem__`/`get`);
writes accept `WritePayload` (create/update) or `ActionPayload` (custom
actions). Strategies bridge: dicts produced by the apply pipeline are
wrapped in `WritePayload` before calling the client; `ServerRecord`
results are flattened via `model_dump()` for the in-place strip / diff
/ preserve passes.

Application use cases depend on one of two Protocols in
`application/ports.py`:

- **`ResourceClient`** is the spec-driven read/write port — `list`,
  `get`, `find`, `find_by_identity`, `create`, `update`, `delete`,
  `action`, `sub_endpoint_request`, `paginate_sub_endpoint`. Methods
  take a domain `ResourceSpec`; the concrete adapter narrows internally.
- **`RawHttpResourceClient`** extends `ResourceClient` with the raw-URL
  escape hatches `request`, `paginate_path`, `request_text` for callers
  that need to construct AWX URLs directly. Today: `ApplyResource`
  (forwards its client to strategies that build nested-endpoint URLs),
  `WatchJob`, and `PollingJobMonitor` (poll job execution endpoints).
- **`JobRecordRepository`** is the read port for AWX execution records
  (`jobs`, `workflow_jobs`, `project_updates`, …). `list(kind=…, params, limit)`
  walks a kind's collection; `get(kind=…, job_id)` fetches one record.
  The concrete adapter (`infrastructure.job_record_repo.JobRecordRepository`)
  wraps `RawHttpResourceClient` and is the only place that knows the
  `Job.kind → AWX collection` mapping.
- **`UnifiedTemplateRepository`** is the read port for AWX's polymorphic
  `/unified_job_templates/` view. `list` walks the aggregate; `get_by_ids`
  bulk-fetches via `?id__in=…`.

`cli/` modules **never** call the raw-URL escape hatches directly —
they route through use cases (`ListJobs`, `GetJob`,
`BrowseUnifiedTemplates`, `GetUnifiedTemplate`, …) which depend on the
narrow read ports above. New use cases default to `ResourceClient`. The
concrete `ResourceRepository` implements both `ResourceClient` and
`RawHttpResourceClient`; the new repos take `RawHttpResourceClient` so
they can build their own paths. Both Protocols type their `spec`
arguments as the domain `ResourceSpec`; infrastructure narrows to
`AwxResourceSpec` via `infrastructure.spec.awx_api_path` whenever it
needs `api_path`. Adding a third infra module that needs `api_path`?
Reuse `awx_api_path` — don't copy the dance.

### kubectl-style envelope

`domain/envelope.py`: `{kind, apiVersion, metadata: {name, organization,
parent?}, spec}`. FK references are by name; the default scope is
`metadata.organization`, but `scope_for` (`application/apply_planner.py`)
also recognises `scope_field="inventory"` and reads `metadata.parent.name`
when the parent is an `Inventory` — that's how Host and Group reconcile
membership FKs (`Group.hosts`, `Group.children`) without an extra metadata
field. Schedule's polymorphic parent and the monomorphic Host/Group
inventory-parent both ride on the same `metadata.parent: IdentityRef` slot.

### Apply is preview-by-default

`application/apply_resource.py` is the orchestrator; the work is split
across four collaborators it composes:

- **`ApplyPlanner`** (`apply_planner.py`) — `plan_identity` and
  `plan_payload`. Projects `resource.spec` to `canonical_fields` and
  resolves FK names to ids; sub-endpoint multi-FKs are stripped from
  the body (the membership reconciler handles them). Also exposes the
  pure `scope_for(ref, resource)` helper shared with `apply_file`'s
  prefetch path.
- **`SecretPreservationPolicy`** (`apply_secret_policy.py`) — second-pass
  secret handling. After `_secret_paths.strip_encrypted` removes
  `$encrypted$` placeholders, the policy decides which top-level fields
  can be safely omitted from the PATCH (AWX retains them) vs which
  carry a sibling change that would clobber the preserved secret
  (rejected at the boundary).
- **`FieldDiff`** (`apply_field_diff.py`) — order-insensitive field-level
  diff. Returns `list[FieldChange]` for the preview; emits
  "preserved existing secret" rows for fields still in
  `preserved_fields` (whether present in `desired` or stripped out
  entirely).
- **`MembershipReconciler`** (`apply_membership.py`) — plans + executes
  multi-FK sub-endpoint membership writes (`Group.hosts`,
  `Group.children`). Membership writes are kept out of the PATCH body;
  associate/disassociate POSTs go through the
  `<api_path>/<id>/<sub_endpoint>/` endpoint.

Writes require `--yes`. The diff is field-level; declared `secret_paths`
(e.g. `inputs.*`, `webhook_key`) carrying `$encrypted$` are stripped
from PATCH and shown as `(preserved existing secret)` rows.
`$encrypted$` at *undeclared* paths fires a stderr warning and is
dropped (paranoid net).

### `ApplyStrategy`

A Protocol in `application/ports.py`. The default strategy uses plain CRUD;
`ScheduleApplyStrategy` POSTs against `<parent_path>/<parent_id>/schedules/`
for create and PATCHes the global `/schedules/<id>/` for update.
`InventoryChildApplyStrategy` (used by `Host` and `Group`) follows the same
shape: creates POST `/inventories/<id>/<api_path>/` so the `inventory` FK
is implied by the URL and never carried in the body; updates use the
global `/<api_path>/<id>/` endpoint. Each spec names its strategy;
`infrastructure/strategy_resolver.py` injects the concrete instance.

### Sub-endpoint multi-FK reconciliation

An `FkRef(multi=True, sub_endpoint="…")` (e.g. `Group.hosts`,
`Group.children`) declares a many-to-many edge that AWX manages via `POST
/<api_path>/<id>/<sub_endpoint>/` with `{"id": <member>}` to associate or
`{"id": <member>, "disassociate": true}` to remove.
`MembershipReconciler.plan` (`apply_membership.py`) diffs desired (from
`resource.spec[<field>]`) against existing (one GET per FK ref) and appends
`FieldChange` rows to the apply diff; `MembershipReconciler.execute` issues
the writes after the strategy's create/update succeeds.

Membership fields are *kept out of the PATCH body* so AWX never sees
`hosts: [...]` on a Group write — body and membership writes are
independent. An *absent* membership field is left unmanaged; an *empty
list* explicitly clears membership. Sub-endpoint refs do not contribute
apply-order edges, so `Group.children → Group` self-references don't trip
the cycle detector.

### `Catalog`

Also a Protocol; `infrastructure/catalog.py` provides the static
`AwxResourceCatalog` over `ALL_SPECS`. Use cases never import
infrastructure — CLI wires concrete adapters at the composition root
(`cli/_context.py`).

### Bulk FK prefetch

`FkResolver.prefetch`: before the apply loop in
`application/apply_file.py`, the FK plan derived from each doc's `fk_refs`
is pre-fetched in one paginated `list` per `(kind, scope)`. Per-record
lookups still fall through on cache miss; prefetch failures are
best-effort (the per-call path is the authoritative one).

### Restore fidelity tiers

`full` (JT, Project, Schedule, Host, Group), `partial`
(WorkflowJobTemplate), `read_only` (Credential, Organization, Inventory,
CredentialType, plus catalog-only stubs ExecutionEnvironment, Label,
InstanceGroup with `commands=()`). Saves below `full` echo the tier to
stderr and embed an inline YAML comment.

### Apply ordering

For multi-doc files / directories: derived topologically from each spec's
`fk_refs` (`application/apply_file._topological_sort`), with `ALL_SPECS` in
`infrastructure/specs/__init__.py` as the tie-breaker — currently yielding
`Organization → CredentialType → Credential → Project → Inventory → Host →
Group → JobTemplate → WorkflowJobTemplate → Schedule`. Self-referencing
sub-endpoint multi-FKs (e.g. `Group.children → Group`) are excluded from
the dependency graph, so re-ordering `web-servers` and `app-tier` Group
docs in the same file is safe — membership is reconciled after each
create.

The catalog-only stubs `ExecutionEnvironment`, `Label`, and `InstanceGroup`
sit between `Group` and `JobTemplate` in `ALL_SPECS` for `FkResolver`
lookups but are excluded from apply/save flows by their `commands=()`
setting.

## Job execution and `--track`

Polling lives in `PollingJobMonitor` (`infrastructure/job_monitor.py`),
the concrete `JobMonitor` adapter. Cadence is **2.0 s** to match
`WatchJob`. AWX v2 has no SSE/websocket — "live" is always polling.

`launch --track / -t` on every launch-capable kind streams events to
**stderr** (rendered by `cli/_event_render.render_event_text` as
`PLAY [..]` / `TASK [..]` / two-space indented
`ok|changed|failed: <host>` lines; ANSI on TTY, plain when piped or
redirected, no TUI), then **propagates job status into the exit code**:
exit 0 only when every tracked job ends `successful`; otherwise exit 1. `--wait` keeps
its old quiet-block semantics; `--monitor` (the v0 silent alias for
`--wait`) is removed.

**Multi-template launch** (`launch a b c --track` or `--wait`) splits
the body into a sequential launch phase and a parallel monitor phase.
For two or more templates, `cli/resource_commands._drain_parallel`
(`--track`) and `_wait_parallel` (`--wait`) drive a
`ThreadPoolExecutor`; wall-clock collapses from `O(sum(durations))` to
`O(max)`. `_drain_parallel` multiplexes per-job event streams onto a
`queue.Queue`; the main thread is the only one that prints, with each
line carrying a `[<template>] ` prefix (via
`render_event_text(ev, prefix=…)`) so concurrent stderr stays
disambiguable. Single-template launches keep the zero-overhead
sequential path. Same thread-safety guarantees as the parallel `ThreadPoolExecutor`
branch in `RunTestSuite.__call__` (`application/test/runner.py`):
`httpx.Client` is documented thread-safe and `PollingJobMonitor`'s
polling methods are stateless per call.

## `unified-templates`: deliberately outside the framework

Implemented in `cli/unified_templates_commands.py` (sibling of
`test_commands.py`), **not** via `make_resource_app` — the factory bakes in
CRUD assumptions `/unified_job_templates/` can't satisfy. No `ALL_SPECS`
entry, no catalog registration. Launch dispatch is intentionally out of
scope: the per-kind sub-apps (`job-templates launch`, `projects update`,
…) already cover that path. User-facing reference: see
[`docs/awx.md`](../../docs/awx.md).

## `workflow-templates nodes`: read-only inspector attached post-factory

`cli/workflow_node_commands.register_nodes_command(parent)` attaches a
`nodes` command to the factory-built `workflow-templates` sub-app at
the bottom of `cli/commands.py`'s `ALL_SPECS` loop. The command walks
`/api/v2/workflow_job_templates/<id>/workflow_nodes/` via the
`RawHttpResourceClient.paginate_path` escape hatch (same mechanism
`unified_template_repo.py` uses), so no new spec-driven CRUD wiring is
introduced — the workflow node graph is still v0.5 territory for
apply/save (`spec.fidelity = "partial"`). Layering: domain DTO
`WorkflowNode` in `domain/workflow_node.py`; port
`WorkflowNodeRepository` in `application/ports.py`; use case
`ListWorkflowNodes` in `application/list_workflow_nodes.py` (cycle-
guarded BFS with optional `max_depth`); concrete adapter in
`infrastructure/workflow_node_repo.py`. The spec object
(`WORKFLOW_JOB_TEMPLATE_SPEC`) is imported only at the CLI
composition root and passed into the use case, preserving the
`application → infrastructure` import ban. User-facing reference:
[`docs/awx.md`](../../docs/awx.md).

## Test framework (`untaped awx test`) runner internals

User-facing reference (file shape, variables, name resolution, pass-through
warnings) is in [`docs/awx.md`](../../docs/awx.md). Internals:

- **Runner phases** (`application/test/runner.py`): `load → plan →
  prefetch → resolve → launch+wait`. Resolution finishes in the main
  thread before any worker is spawned (`FkResolver`'s caches aren't
  thread-safe). Workers only do `RunAction(spec, ..., payload=…)` +
  `WatchJob(job, timeout=…)` against a shared `AwxClient` (`httpx.Client`
  is documented thread-safe).
- **Result classification**: `result ∈ {pass, fail, error, timeout}`,
  separate from AWX's raw `job_status`. Exit code 0 only when every case
  has `result == "pass"`.
- **Wiring**: `cli/test_commands.py` is the composition root; it builds
  `LoadTestSuite` (with `DefaultParser`, `resolve_variables`,
  `TyperPrompt`), `ResolveCasePayload`, and `RunTestSuite` from
  `AwxContext`. The parser/vars-resolver/prompt are application-layer
  Protocols (`application/test/ports.py`); concrete adapters live in
  `infrastructure/test/`.
- **`!ref` escape hatch** (in addition to `fk_refs`): `RefSentinel` lives
  in `domain/test_suite.py`; the constructor is in
  `infrastructure/test/parser.py`. Structurally distinct from a dict, so
  user content like `{name: Alice}` is never misinterpreted.
- **Catalog stubs** (`ExecutionEnvironment`, `Label`, `InstanceGroup` in
  `infrastructure/specs/_support.py`) exist purely so `FkResolver` can map
  names → ids; they have `commands=()` and no CLI sub-app.

## Tests

The in-memory `FakeAap` fixture (`tests/conftest.py`) drives end-to-end
CLI flows.

## See also

- [Root AGENTS.md](../../AGENTS.md) — 4-Layer DDD, Hard Rules, recipes
- [`docs/awx.md`](../../docs/awx.md) — user-facing setup and command
  reference (covers `jobs`, `unified-templates`, `test`)
- [`packages/untaped-core/AGENTS.md`](../untaped-core/AGENTS.md) —
  profiles, TLS, `resolve_verify`
