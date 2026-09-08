# AWX mutation integrity

AWX configuration writes use a shared application engine. A batch is prepared
before execution: resource IDs, foreign keys, create parents, payloads, secret
preservation and membership changes are fixed in memory. The single-resource
and file adapters use this same path.

Execution re-reads affected fields and memberships of every existing target
before the first write. A conflict or deletion stops the entire batch without
writes. This check does not prevent a controller change between requests and
does not provide a transaction or rollback.

Creation dependencies execute before their dependents. Memberships execute after
resource bodies exist, so groups can reference each other. Bodies and memberships
are verified independently. A successful body with a failed membership is a
partial result, retaining the resource ID. Runtime failures stop new scheduling
unless continuing on error was requested; already running results are collected.
Concurrency is bounded at ten requests.

Owned maps are replacements: omitted keys must be absent after writing and an
empty map clears the value. Body lists retain order. Memberships are sets unless
the resource specification declares an ordered relationship. Server enrichment
is accepted only for explicitly declared fields. Known current and newly entered
secret values are redacted from previews, result changes and controller errors.

## Application interface

`BatchMutationEngine.prepare(resources, mode="apply", existing=None,
preserve_existing_fk_ids=False)` returns an in-memory `MutationPlan`.
`execute(plan, continue_on_error=False, parallel=1)` executes that same plan.
Callers display each operation's `preview` and `presentation_payload`; the raw
payload and snapshot are execution-only. Execution binds typed creation
references separately, without mutating planned targets.

Callers editing selected records supply the exact records through `existing` and
use `mode="patch"` or `mode="edit"` to prohibit creation. Apply files use
`ApplyFile`; single documents use `ApplyResource`. Command availability and flags
remain discoverable through `untaped awx --help` and each subcommand's help.
