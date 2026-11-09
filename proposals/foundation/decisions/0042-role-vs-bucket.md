# 0042. Role vs bucket; what a plugin declares

- **Status:** amended · 2026-11-05 · the maintainer
- **Rests on:** [0022](0022-ranked-buckets.md) ranked buckets; [0026](0026-kernel-stripped.md) the kernel stripped
- **Amended by:** [0048](0048-kernel-requirement-rule.md): `uses` is no longer a kernel declaration. The kernel requires only `offers` and `fills`; `uses` is optional and belongs to whichever plugin wants it.

## Problem

After defining the host as a role ([0041](0041-process-slot-and-host.md)), the maintainer asked for a rule: when is something a role, and when is it a bucket? Engine, user and host had been drifting between the two.

## Decision

**Role vs bucket:**

- a **bucket** is what code *is*, and fixes which way its dependencies may point. Each piece of code is in exactly one;
- a **role** is a relationship code has with something else (fills, uses, opens scopes, checks). Roles combine freely.

Test: can one package be both under the same dependency rules? If yes, it's a role. git-cli fills `repo_store` and has its own commands: one bucket (parties), two roles (engine and user).

Roles named at the time: engine, user, host, provider, resolver, checker. The notation is neither.

**What a plugin declares** (the maintainer's suggestion): one field per role.

```python
class Jira(Plugin):
    offers = (JiraSource,)            # owner
    fills  = (List, Show)             # engine / provider; commands fill the command grammar's point
    uses   = (settings_store,)        # user  ← removed from the kernel by 0048
```

```python
class Jira(Plugin):
    commands = (List, Show)           # ✗ a special field for one kind of filler
```

Commands are `fills` of the command grammar's extension point, no longer a special `commands` field. Host is the one undeclared role: it belongs to running code.

## Consequences

- At the time, `uses` made the Service Locator answer concrete ("jira uses `settings_store`, which nothing fills", before anything runs). [0048](0048-kernel-requirement-rule.md) moved that earliness to a plugin (e.g. a doctor's words); the same fact still appears at the call as `Unfilled`.
- Rank 3 was renamed "doing" for now; the naming went on in [0046](0046-rank-3-parties.md).
