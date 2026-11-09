# 0009. Everything runs unattended

- **Status:** accepted · settled before 2026-11-05 (foundation point 8; the maintainer: "Perfect.") · the maintainer
- **Rests on:** [0001](0001-make-your-own.md), the goals (agents and scripts drive untaped; swappable front ends)

## Problem

AGENTS.md: "Everything untaped does can run unattended, so scripts and agents can drive it." With swappable front ends, a question asked at a terminal must also work where there is no terminal (MCP, a hub, a script).

## Decision

**Every question is a request.** A front end answers it with a prompt, a dialog or a form. Or a flag, an env var or a config value answers it ahead of time.

```python
# ✗ a plugin asks the terminal directly
answer = input("Delete 3 repos? [y/N] ")

# ✓ it declares that consent is needed; whoever runs it answers
class Prune(Command):
    confirm: Confirm          # --yes, a prompt, an MCP parameter, a hub form
```

## Consequences

- This rule is what makes front ends swappable.
- untaped treats people and agents alike. What it says about a step names facts (it asks for a secret, it needs admin rights), never who should act.
- Questions are later reached through a slot bound in the run's scope (the prompts words: a terminal prompt, an MCP request, a flag answered ahead of time; see [0038](0038-settings-and-no-run-family.md)).
