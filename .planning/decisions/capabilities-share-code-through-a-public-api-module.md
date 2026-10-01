# capabilities share code through a declared public module

Decision ID: `dec_01a0f840de43717bb95befe29371c16d`

A capability may import another capability only through that capability's
`api.py` module; each importing pair is listed in the import-boundary test.
Core helpers still come only from `untaped.capability_api`.

Rationale: the earlier rule routed every collaboration through
`capability_api`, so one consumer needing one provider's data (the workspace
picker listing GitHub repositories) would have required a generic core hook
with a single implementer. A declared, closed module keeps encapsulation and
reviewability without that indirection.

Constraints:

- One public module per capability, named `api.py`, with a closed `__all__`
  pinned by a test.
- Pairs are explicit and one-way; no cycles.
- When a second provider of the same thing appears, extract a protocol into
  core then, not before.

## Related decisions

- Refines: [unified application and capability composition](unified-application-and-capability-composition.md)
