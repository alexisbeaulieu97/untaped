# 0056. Multi-host is parked; rules wait in Pending

- **Status:** accepted · 2026-11-08 · the maintainer
- **Rests on:** [0052](0052-call-shape.md) the `Call` shape; [0055](0055-document-shape.md) the shape of this document
- **Amends:** [0052](0052-call-shape.md): the multi-host guardrail is no longer in force.

## Problem

[0052](0052-call-shape.md) parked multi-host "beyond the guardrail", so one multi-host rule stayed among the kernel's rules. It could not be true as written. Every shape crossing `Dispatch` had to be plain data, rebuildable on a machine without the providers, yet `Err(error)` carries whatever a provider raised. An error class installed only on the runner can't be rebuilt on the hub.

```python
Reply(PluginId("acme-git"), Err(TokenExpired()))        # git's words are on the hub: rebuilt
Reply(PluginId("acme-git"), Err(AcmeSocketGlitch()))    # acme's code isn't: it can't be
```

The maintainer: single-host is settled first, so the multi-host rules should wait with the rest of multi-host.

## Decision

- **Rules about calls leaving a machine are out of the rule pages.** They wait in a **Pending** section on the page they would return to, and are not in force. They come back when multi-host is designed.
- **Direction for errors when that happens: the real error when its class can be rebuilt on the receiving machine, otherwise a kernel fact** holding its type name and message (placeholder name: `Opaque`). The forwarding dispatcher does the conversion, so how errors travel stays its design.

## Rejected

For errors crossing machines:

- **Exempt errors from the guardrail:** askers on a hub would see different things per dispatcher, against "askers see one shape whoever carries the call" ([0051](0051-reply-and-result.md)).
- **Always plain text in `Err`:** even on one machine, askers would lose `except TokenExpired` and match on strings, against typed references.

## Consequences

- **The shapes stay.** F3 and `PluginId` were chosen with a hub in view, and the goal of runners and hubs still stands. Parking lifts the promise, not the shapes. F1's only recorded rejection is a hub, so it is the thing to look at again if multi-host is ever dropped.
- `Err` carrying the provider's error as is no longer conflicts with anything.
- A hub as a host running per-user scopes on one machine is not multi-host. Those rules stay.
- Pending holds rule text only, not questions or tasks. Whatever is still pending when this design moves into the repository becomes a GitHub issue, since the repository keeps no backlog ([0055](0055-document-shape.md)).
