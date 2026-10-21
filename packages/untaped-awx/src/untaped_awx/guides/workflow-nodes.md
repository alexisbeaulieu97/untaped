---
description: "Read before writing or applying a workflow template's node graph: node and prompt fields, how export writes the graph, how apply reconciles it, its preview, and what it refuses."
---

A `WorkflowJobTemplate` document holds its node graph under `spec.nodes`, by
name:

```yaml
kind: WorkflowJobTemplate
apiVersion: untaped.dev/awx/v1
metadata: {name: Release, organization: Default}
spec:
  description: Build, deploy, verify
  inventory: Production
  nodes:
    - id: approve-prod
      approval: {name: Approve production, timeout: 3600}
      success: [deploy]
    - id: deploy
      run: {job_template: Deploy}
      prompts: {limit: "web*", extra_vars: {version: 3}, credentials: [ssh]}
      success: [verify]
      failure: [rollback]
    - id: rollback
      run: {job_template: Shared rollback, organization: Ops}
    - id: verify
      run: {job_template: Smoke check}
      all_parents_must_converge: true
```

| Field | Meaning |
|---|---|
| `id` | Required. The node's key within the workflow (AWX's node `identifier`). Apply matches nodes by it, so keep it stable. |
| `run` | What the node runs: exactly one of `job_template`, `workflow_job_template`, `project` (a project update), `inventory_source` (an inventory update) or `system_job_template` (a management job), by name. |
| `organization` | In `run`: the template's organization when it differs from the workflow's; `null` for a template without one. Management jobs take none. |
| `inventory` | In `run`, with `inventory_source` only (and required there): the inventory holding the source; `organization` is then that inventory's. In `prompts`: the inventory to run against. |
| `approval` | Instead of `run`: an approval step. |
| `name`, `description`, `timeout` | In `approval`: what approvers see, and seconds before it times out (`0`, the default, waits forever). |
| `prompts` | Launch values the node passes to what it runs (not allowed on approvals); AWX accepts one only when the template prompts for it on launch. |
| `success`, `failure`, `always` | Ids of the nodes that run next when this node succeeds, fails, or either. |
| `all_parents_must_converge` | Run only once every parent has reached this node; default `false`. |

`prompts` fields:

| Field | Meaning |
|---|---|
| `inventory`, `credentials`, `labels` | Names in the workflow's organization, or `{name, organization}` for one elsewhere (an export writes that form for you). |
| `instance_groups`, `execution_environment` | Global names; instance groups in fallback order. |
| `extra_vars` | A mapping (AWX's node `extra_data`). |
| `limit`, `scm_branch`, `job_tags`, `skip_tags` | Strings. |
| `job_type` | `run` or `check`. |
| `verbosity` | 0 to 5. |
| `diff_mode` | `true` or `false`. |
| `forks`, `job_slice_count`, `timeout` | Numbers (`timeout` in seconds). |

Export:

- writes the graph from the roots down, nodes that are ready together in `id`
  order, so a replaced node keeps its place in a git diff; edge lists are
  sorted and defaults left out;
- leaves out, with a warning, a node whose template was deleted, and the edges
  into it.

Apply reconciles the graph by `id`, after the workflow itself is written:

1. creates missing nodes (an approval through AWX's
   `create_approval_template`);
2. patches changed nodes (an approval's name, description or timeout on its
   approval template; credentials, labels and instance groups as apply
   replaces relationship lists, `untaped agents awx documents`);
3. deletes nodes no longer listed, except those it cannot name;
4. removes, then adds, edges.

- A node that turns into an approval, or back, is deleted and created again,
  and its edges are re-added.
- `nodes: []` deletes every node untaped can name; a document without `nodes`
  leaves the graph alone.
- Survey-password `extra_vars` read back as `$encrypted$`. A placeholder, in
  the document or in AWX, matches any value, so re-applying an export changes
  nothing and a document holding the plain password converges too.
- A new node cannot take the placeholder: it is created without that
  variable, with a warning.
- The templates the nodes run are ordering dependencies: a job template created
  by the same `apply` is created before the workflow that runs it, and a
  workflow whose template fails is skipped.

The preview shows one row per change, by name. Edges that go away with a
deleted node, or come back with a replaced one, are not listed:

```text
WorkflowJobTemplate/Release id=100 scope=org=Default: planned
  nodes[notify]: null → {"run":{"job_template":"Notify"}} (create)
  nodes[deploy].prompts.limit: "web*" → "web1"
  nodes[approve-prod].approval: {"name":"Approve production","timeout":3600} → {"name":"Approve production","timeout":600}
  nodes[rollback]: {"run":{"job_template":"Rollback"}} → null (delete)
  nodes[verify].always: [] → ["notify"]
```

Refused before any write:

- a duplicate `id`, an edge to an unknown `id`, the same pair of nodes linked
  twice, or a cycle (`nodes form a cycle: a → b → a`);
- two workflows created by the same `apply` that run each other
  (`workflow nodes form a recursion: A → B → A`);
- a template name that does not exist (with a "did you mean" hint).

A node write that fails after the workflow itself was written leaves a
`partial` row naming the node and step (`nodes[deploy] update: …`).
Re-running the same apply picks up from what AWX then holds and finishes the
graph. A graph that does not read back as declared fails with `workflow
nodes did not converge: nodes[deploy].prompts.limit`.
