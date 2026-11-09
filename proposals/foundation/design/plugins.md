# Plugins: choosing engines (`untaped-plugins`)

> Draft. The shape of a user's preferences is not decided.

The kernel never chooses between engines. When several fill one slot and nothing is bound in the scope, it asks every `Resolver` ([architecture](../architecture.md#resolving-a-slot)). `untaped-plugins` is our first-party resolver ([0029](../decisions/0029-resolvers.md), [0032](../decisions/0032-picks-and-order-are-resolvers.md)).

- It answers from **what the user declared**, and answers nothing when that doesn't decide. It never invents a preference: ✗ "first-party wins".
- Picks for a slot, and the order of extend providers, belong to resolvers, never to the slot owner's settings. The per-contract `rank` settings go away.
- It owns the `untaped plugin ...` commands (list, use, ...) and the doctor check that shows each slot's state.
- Other resolvers may exist, such as a company policy resolver. They combine by the kernel's rule: one distinct answer wins; no answer is `Undecided`; different answers are `Ambiguous`.
- An embedded program that wants a choice writes a tiny resolver, or binds the slot in its scope.

**Not decided** (it belongs to this plugin's own design, together with the settings questions): the file format, the section name, whether a preference is one global list or per slot, per profile or per project, and what users name.

## Bootstrap

The slots that resolvers reading settings can't decide (the settings engine, and what it and the resolvers need) form the **bootstrap closure** ([0030](../decisions/0030-loop-and-bootstrap.md)). They are decided by being the only engine installed, or by resolvers that need nothing from settings, such as the CLI host's `EnvResolver`, which reads the process environment through the `process` slot. Doctor reports the closure.
