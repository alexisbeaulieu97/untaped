# 0037. Concurrency: `Capacity` and "stopped"

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0008](0008-the-run-rule.md) the run rule · [0024](0024-grammar-bucket.md) the grammar bucket (G4: policy is a setting)
- **Corrected by:** [0038](0038-settings-and-no-run-family.md) `Capacity` is delivered through a slot resolved in the kernel scope, not "carried by a run"

## Problem

Today contracts take budgets in their signatures (`probe_refs(..., parallel=4)`). That breaks G4 (policy is a setting) and multiplies when calls nest. Separately, today one error (`OperationCancelledError`) means both "the user said no to `Confirm`" and "the run was stopped".

## Decision

- **Budgets leave contract signatures.** One shared budget per run, set by the host from what the user declared; providers draw from it. Providers' hard limits (rate limits) stay facts they report.
  - ✗ `def probe_refs(self, refs: list[Ref], parallel: int = 4)`
  - ✓ `def probe_refs(self, refs: list[Ref])` — the provider draws from the scope's `Capacity`
- **`Capacity` belongs to a concurrency package** with its own words, grammar and engine (the maintainer's correction: a mechanic several features need is solved once by its owner). By the package-line rule it separates: it changes for different reasons than the run and can be used apart. `Capacity` means "N things at once", shared by the whole scope, so nested calls don't multiply.
- **"Stopped"** (Ctrl-C, a hub cancelling a job) moves to the concurrency package with `Capacity` (structured concurrency treats cancel scopes as concurrency: Trio, anyio).
- **"Declined"** (a user answering no to `Confirm`) stays in the command grammar as its own word.

## Rejected

- **`Capacity` in the Run words:** the maintainer's correction above.
- **A per-call budget as a setting of each contract:** still multiplies when calls nest.

## Consequences

- Today's `bounded_map` and `bounded_iter` (`sdk/_concurrency.py`) are the concurrency engine; `Parallel` (`-j/--parallel`) is a command-level way to set the capacity.
- The concurrency engine's duty to carry the scope into workers: [0040](0040-scope-bindings-noscope.md).
- Today's single `OperationCancelledError` carrying both meanings is an input for the code assessment.
