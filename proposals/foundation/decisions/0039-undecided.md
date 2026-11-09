# 0039. `Undecided`

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0029](0029-resolvers.md) ambiguity fails by default; [0030](0030-loop-and-bootstrap.md) the loop rule

## Problem

[0029](0029-resolvers.md) gave a slot with several engines one failure outcome, `Ambiguous`. That outcome covered two different situations:

- the resolvers gave **different** answers;
- the resolvers gave **no** answer at all (none installed, none knew, or the only one hit `Loop`).

While settling the loop rule, the maintainer said: "ambiguous is when you get more than one answer." Several engines with no answer is not "more than one answer", so it needed its own outcome.

## Decision

Resolving a slot with several engines ends in exactly one of:

| Resolvers' answers | Outcome | What it tells the user |
|---|---|---|
| exactly one distinct answer, naming an installed engine | that engine | — |
| none | `Undecided(slot, engines)` | nothing chose: declare a preference |
| more than one distinct answer | `Ambiguous(slot, answers)` | the resolvers disagree |

```python
# two engines installed, no resolver says anything
ask(repo_store)   # ✗ Ambiguous(slot=repo_store, answers=())   one word, two meanings
ask(repo_store)   # ✓ Undecided(slot=repo_store, engines=(GitCli, AcmeGit))
```

Both are facts returned as data, only where the slot is used. Everything else keeps working.

## Consequences

- Kernel rule 3 and invariant 12 name both outcomes.
- The fix a user is offered differs: `Undecided` asks for a preference; `Ambiguous` asks the user to reconcile resolvers that disagree.
- A resolver that hits `Loop` gives no answer, so it contributes to `Undecided`, not `Ambiguous` ([0030](0030-loop-and-bootstrap.md)).
