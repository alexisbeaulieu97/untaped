# capabilities share code through a declared public module

A capability may import another capability only through that capability's
`api.py` module; each importing pair is listed in the import-boundary test.
Core helpers still come only from `untaped.capability_api`.

Rationale: the earlier rule routed every collaboration through
`capability_api`, so one consumer needing one provider's data (the workspace
picker listing GitHub repositories) would have required a generic core hook
with a single implementer. A declared, closed module keeps encapsulation and
reviewability without that indirection.

Constraints: AGENTS.md Hard Rule 2 states the rule. In addition, each
`api.py` keeps a closed `__all__` pinned by a test.

## Related decisions

- Refines: [unified application and capability composition](unified-application-and-capability-composition.md)
