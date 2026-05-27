# AGENTS.md — `untaped-awx`

Internals of the AWX/AAP bounded context for AI agents and contributors.
For user-facing setup and command reference, see
[`docs/awx.md`](../../docs/awx.md). For workspace-wide rules (4-layer DDD,
Hard Rules, recipes), see the [root `AGENTS.md`](../../AGENTS.md).

## `AwxConfig` — package-local config

`AwxConfig` (`infrastructure/config.py`) is the package-local config struct
(`base_url`, `token`, `api_prefix`, `default_organization`, `page_size`).
The canonical bridge is `AwxConfig.from_settings(settings)` — a single
classmethod that mirrors `untaped_core.settings.AwxSettings` field-for-field.
Only `cli/` modules read `untaped_core.Settings` (and `infrastructure/config.py`
imports it under `TYPE_CHECKING` purely for the classmethod signature) —
today `cli/_context.AwxContext.__init__` and `cli/commands.py:ping_command`,
plus any future CLI command. `application/`, `infrastructure/`, and `domain/`
depend on `AwxConfig` so `untaped-awx` is extractable as a standalone library.

Adding a new field is a four-place edit (`AwxSettings`, `AwxConfig`, the
`from_settings` body, and `test_config.test_from_settings_field_set_matches_awxsettings`);
the field-inventory test fails CI loudly if you forget one of the first two.

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
(`cli/_launch._add_launch`) enforces this structurally — each
flag whose payload field isn't in the kind's `ActionSpec.accepts` is
passed `Option(hidden=True)` so it's omitted from `--help` while still
being parseable (the runtime guard `_reject_unsupported_launch_flags`
catches a user who passes a hidden flag anyway). The flag→payload-field
mapping, the visibility check, and the value translation all flow
through one table — `LAUNCH_FLAGS: tuple[LaunchFlag, ...]` in
`cli/_launch.py`. Adding a ninth launch flag is two edits:
one Typer `Option(..., hidden=hidden_by_flag["--nine"])` in
`_add_launch`'s signature and one `LaunchFlag` row in the table
(flag name + `ActionSpec.accepts` key + a `payload_builder` closure
for the CLI-value-to-AAP-field translation). The three downstream
call sites (`_add_launch`'s hidden-flag map, `_reject_unsupported_launch_flags`,
`_build_launch_payload`) walk the table once each so the dispatch
fan-out stays in lock-step.

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
  secret handling. After `_secret_paths.strip_encrypted_in_place`
  removes `$encrypted$` placeholders, the policy decides which top-level fields
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

The same write path is also exposed directly via spec-driven
`<parent> <sub_endpoint> add/remove` subcommands (`cli/membership_commands.py`)
for additive, sync-free use (e.g. `groups hosts add prod-web --stdin`).
`make_resource_app` walks each spec's `FkRef(multi=True, sub_endpoint=…)`
and attaches a nested Typer sub-app via `register_membership_subapp`, so
new multi-FK refs light up these subcommands for free.
`application/manage_membership.py` calls
`MembershipReconciler.post_members` directly with a `disassociate=bool`
kwarg — no `MembershipPlan` construction, no listing existing members
first. `MembershipReconciler.execute` routes the apply pipeline's
two-tuple dispatch through the same `post_members`, so the POST loop
lives in one place. AWX-side idempotency of associate/disassociate
POSTs makes the additive shape safe.

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
best-effort (the per-call path is the authoritative one). The
`AwxApiError` branch in `_prefetch_one` calls the `warn` hook injected
at `cli/_context.py` so the user sees a `warning: FK prefetch for
<kind> [(<scope>)] failed (...)` line on stderr instead of a silent
fallback to N per-record GETs.

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

### Apply parallelism

Phase 1 is parallelisable **within a kind** but serial **across kinds**.
`ApplyFile.__call__` walks the topologically sorted docs and buckets
them by kind via `defaultdict(list)` (Python dict insertion order
preserves the topo order from `_topological_sort`). `defaultdict`
instead of `itertools.groupby` is deliberate: `groupby` requires
consecutive same-kind docs, an invariant that lives in a different
module — a future re-sort that interleaves kinds would silently split
a kind into multiple groups, hurting parallelism without breaking
tests. Each kind group is dispatched through `_apply_kind`, which
uses a `ThreadPoolExecutor` when `parallel > 1` and `len(docs) > 1`.
Outcomes are keyed by input index so the returned list matches input
doc order regardless of `as_completed` ordering. On `fail_fast=True`,
queued futures are cancelled but in-flight workers run to completion
(matching `_drain_parallel`'s semantics); a post-loop drain pulls their
outcomes out of the futures so a `write=True` apply never silently loses an AWX
mutation. The pool is capped at `APPLY_PARALLEL_CAP=10` to match
`httpx.Client`'s default `max_connections=10` — anything higher just
blocks on connection acquisition. The CLI clamps to this cap via
`untaped_core.clamp_parallel` (shared with workspace `sync`/`foreach`,
policy `"httpx.Limits.max_connections=10"`); `ApplyFile.__init__`
re-applies `min(parallel, APPLY_PARALLEL_CAP)` as a programmatic-caller
safety net.

Phase 2 (membership reconciliation) stays serial. Reasons:

- Membership writes can reach across kinds (`Group.hosts` needs both
  `Group` and `Host` live), so parallelising within a kind doesn't help
  the dependency-driven serialisation that phase 2 needs.
- Sub-endpoint POSTs are per-record; contention there isn't a win at
  typical apply sizes.
- Serial phase 2 keeps `reconcile_memberships`'s ordering, which
  simplifies error attribution back to the offending doc.

Thread-safety relies on the same guarantees the "Job execution and
`--track`" section above already documents for `_drain_parallel`:
`httpx.Client` is thread-safe, `ApplyResource` has no per-call
attribute rebinds (the `strip_encrypted` pass mutates a per-call
deepcopy — see issue #10; structurally pinned by
`test_apply_resource_has_no_per_call_attribute_rebinds`), and
`FkResolver`'s two caches are guarded by `self._cache_lock` across the
read + repo call + write window so two workers racing on the same
`(kind, name, scope)` collapse into one repo lookup (pinned by
`test_concurrent_name_to_id_dedups_repo_calls_under_contention`).
`ApplyOutcome` is frozen (`domain/outcomes.py`), so phase 2's outcome
rewrites go through `model_copy(update={...})` instead of in-place
mutation. Parallelising phase 2 itself still requires an index-keyed
collector (the same shape phase 1 uses) so two threads can't race on
the same `outcomes` list slot — frozen rules out the in-place mutation
race, not the slot race.

## Delete: preview-and-confirm with a `--yes` fast path

`cli/_delete.py` follows the apply pipeline's preview-by-default ethic:
without `--yes`, each identifier is resolved via GET (so the prompt
lists the names being deleted), the user confirms, then a per-id
DELETE runs. Under `--yes` (no prompt to surface the name), the
resolve phase skips the per-id GET — AWX's DELETE returns the same
`not found: <url>` shape on a missing id, so one bulk `?id__in=…`
prefetch (`GetResource.by_ids`) keeps the post-DELETE row's `name`
column populated without N round trips. `--by-name` opts out of the
fast path; identifiers go through the normal name lookup.
`--stdin` requires `--yes` or `--dry-run` — confirmation can't share
stdin with the identifier stream. Every emitted row leads with `id`
so `--format raw` pipes the deleted ids to downstream xargs/jq.

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
For two or more templates, `cli/_parallel._drain_parallel`
(`--track`) and `_wait_parallel` (`--wait`) drive a
`ThreadPoolExecutor`; wall-clock collapses from `O(sum(durations))` to
`O(max)`. Both share the executor / future-collection / error-wrap
scaffolding via `_drain_parallel_with_worker(jobs, worker_fn, *,
while_running=…)` — each caller contributes only its unique mechanics
(queue + print loop for `--track`; `WatchJob` lambda for `--wait`).
`_drain_parallel` multiplexes per-job event streams onto a
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
`ListWorkflowNodes` in `application/list_workflow_nodes.py` (BFS with
per-entry ancestor tracking and optional `max_depth`); concrete adapter
in `infrastructure/workflow_node_repo.py`. The spec object
(`WORKFLOW_JOB_TEMPLATE_SPEC`) is imported only at the CLI
composition root and passed into the use case, preserving the
`application → infrastructure` import ban. User-facing reference:
[`docs/awx.md`](../../docs/awx.md).

**Cycle vs shared sub-workflow.** Both end up at the "child already
in `listed`" check, but they're not the same incident: a true cycle is
when the child is in the *current* path's `ancestors` (warn + skip);
a diamond is when the child is in `listed` but *not* in `ancestors`
(skip silently — same sub-workflow legitimately referenced from two
parents). Conflating them produced false-positive cycle warnings every
time a workflow contained two nodes pointing at the same child.

**Type discriminator normalisation.** AWX returns the *job* (execution)
discriminator on a node's
`summary_fields.unified_job_template.unified_job_type` — `"job"`,
`"workflow_job"`, `"project_update"`, `"inventory_update"` — not the
*template* type. `normalise_unified_job_type` in
`domain/workflow_node.py` maps these to the template-type
discriminator (`WorkflowNodeType` Literal: `"job_template"`,
`"workflow_job_template"`, `"project"`, `"inventory_source"`), and
returns `None` for unknown values so the recursion guard never
descends into kinds we don't recognise.

**`--stdin` for multi-root.** `nodes` accepts multiple workflow
names/ids on stdin (`untaped awx workflow-templates list -f raw -c id
| untaped awx workflow-templates nodes --stdin --recursive`),
concatenating each root's node tree in input order. Identifier
resolution goes through `untaped_core.read_identifiers` so the same
"exactly one source" + "non-empty stdin" contract applies as on
factory-built `list`/`get`. Per-root failures emit
`warning: <id>: <exc>` to stderr and set a process-wide non-zero
exit; other roots still emit their rows. The factory's `list
--stdin` uses `resolve_each` (`untaped_core.cli`) for this pattern,
but `resolve_each` wraps a `Callable[[str], R] -> list[R]` shape
that maps each id to a single record — `nodes` produces a
`list[WorkflowNode]` per root, so the loop is hand-rolled here.
Errors land on the same stderr channel `resolve_each` uses; only
the per-id row shape differs.

**Cross-root dedup is not done.** Each root starts BFS with fresh
`listed`/`ancestors` state, so a sub-workflow referenced from two
roots appears in both roots' output. Within-root cycle/diamond
handling (above) doesn't carry across the loop because callers
asking "which roots reference template X" need both rows. To
collapse duplicates downstream, pipe through `sort -u`.

**`--filter` for server-side scoping.** `nodes` accepts repeatable
`--filter KEY=VALUE` flags, parsed by `parse_kv_pairs` and passed
verbatim to AWX as query params on each `workflow_nodes` GET
(Django-style, same shape as `list --filter`). With `--recursive`,
the filter applies at every BFS level — a filter that excludes
sub-workflow rows (e.g. `unified_job_template__name__in=t_foo` when
sub-workflows aren't named `t_foo`) will prune them and stop the
descent at that node. Construct OR-style filters
(`or__unified_job_template__name__in=…` +
`or__unified_job_template__unified_job_type=workflow_job`) or
post-filter on the output to preserve full recursion. The
trust-the-user pass-through mirrors `list --filter`'s contract.
The reverse-join approach (`workflow-templates list --filter
workflow_nodes__unified_job_template__name__in=…`) is rejected by
AWX — `workflow_nodes` isn't a filterable relation on the workflow
templates resource — so `nodes --filter` is the supported path for
"which workflows reference these JTs?" queries.

**`summary_fields` projection.** Each `WorkflowNode` row carries the
AWX response's `summary_fields` dict unchanged (populated in
`_build_node` at `application/list_workflow_nodes.py`), so the same
dotted-path columns that work on `list` work here:
`-c summary_fields.workflow_job_template.name` for the *immediate*
parent workflow (per-level, not the BFS root —
`test_nodes_recursive_summary_fields_carries_per_root_name` pins
this), `-c summary_fields.unified_job_template.description` for the
referenced template, etc. All formats (table/json/yaml/raw) honour
the default column set (`id name type depth`) when `--columns` is
absent, so `summary_fields` never appears in unprojected output and
never bloats the default — users opt in via `-c summary_fields.<path>`.
The typed `name` field continues to flatten the referenced template's
name as a convenience (also reachable as
`summary_fields.unified_job_template.name`).

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
