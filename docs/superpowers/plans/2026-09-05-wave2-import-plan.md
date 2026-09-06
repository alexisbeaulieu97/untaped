# Wave 2 import plan (APPROVED 2026-09-05, revised per review; REV 2 2026-09-06)

Status: APPROVED. Slices: github → jira → awx → ansible → recipe → orchestration.
Stop at R1. No remote publication authorized.

## §2.0 close-out gate (before any slice work)

1. `untaped/AGENTS.md` rewritten to unified-app scope (committed on
   `codex/unified-app-v4`).
2. Orchestration PR #7 state verified live: OPEN, unmerged,
   branch `codex/fix-cross-store-validation`, base `main` — unrelated to the
   unified-app line; no interference.
3. This plan saved at `docs/superpowers/plans/2026-09-05-wave2-import-plan.md`
   in the unified clone, committed.

A senior reviewer (fresh agent, no implementation role) verified all three
before any slice agent was dispatched.

## Per-slice contract (each slice, serialized)

1. Fresh disposable clone; implement the slice per the capability spec; commit.
2. Senior review: PASS or fix-and-re-review. Involved lines only.
3. Cumulative checks: FULL suite, ruff, mypy, and the S-suite scanners that run
   in Wave 1 CI — every accepted slice, no narrowing.
4. OID recorded in the control root.
5. Amendments incorporated (see below).

## Five scoped amendments

1. Ansible GitHub boundary: a narrow, GitHub-owned capability API defined
   BEFORE the ansible slice (reviewed PASS), covering repository inventory,
   ref probing, client operations, settings, and result/error types as
   consumed from `untaped-ansible/src`. The sixteen private-provider helpers
   are NOT the inter-capability interface and are not expanded.
2. Caller inventory timing: caller maps recorded BEFORE implementation from
   the CURRENT tool sources named per slice (4–6 repos pulled, never scanned),
   so no "truncated inventory" fix cycle recurs.
3. Branch freshness (REV 2, 2026-09-06 — supersedes the remote-fetch
   procedure, which is void: `origin` in the control setup is a local bundle
   with no unified-app ref): each slice opens with a CONTINUITY check —
   `HEAD` must equal the last accepted slice OID, `git status` must be
   clean, and the last checkpoint branch must point at that OID; otherwise
   STOP and report. Upstream drift verification against the public
   destination is a SEPARATE check performed at rc1 freeze, not per slice.
4. Stale scan artifacts: fixture-token files already deleted in Wave 1 review —
   no freeze on non-existent paths; slices re-scan and fix what exists.
5. Recovery: `git reset` is explicitly NOT the recovery procedure. Each
   accepted slice OID is recorded and preserved through a local checkpoint
   branch AND a verified bundle; recovery rehearsed in a disposable checkout.

## Slice sources

- github: `untaped-github/src`
- jira: `untaped-jira/src`
- awx: `untaped-awx/src`
- ansible: `untaped-ansible/src`
- recipe: `untaped-recipe/src`
- orchestration: `untaped-orchestration/src`

## Stage-B floor rule

Full wheel + lock + test matrix across Python 3.12/3.13/3.14 retained for
EVERY remaining slice — no per-slice reduction. The interpreter floor binds
only when the complete matrix is green.
