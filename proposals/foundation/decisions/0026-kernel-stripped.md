# 0026. The kernel stripped to the plugin foundation

- **Status:** amended · 2026-11-05 (afternoon) · the maintainer
- **Rests on:** [0020](0020-core-is-the-kernel-only.md) core is the kernel only · [0022](0022-ranked-buckets.md) ranked buckets
- **Amended by:** [0049](0049-kernel-hooks-loaded.md) owner checks (K2) became the `Loaded` moment; owners check their own words when a plugin finishes loading

## Problem

The first draft of the kernel bucket still held profiles, settings and deprecation marks. The maintainer asked whether stability marks are a plugin, then proposed stripping the kernel to the plugin foundation only: everything else a plugin.

## Decision

**Entry test:** without it, plugins couldn't find or call each other.

What stays:
- **K1** find and load plugins; a failure is data for the host;
- **K2** store declarations, and owners check their own;
- **K3** contracts and slots, resolved with the choices the kernel is *given*;
- **K4** one call point (`ask`) and what happens around it, with marks passed through uninterpreted.

What moves out:
- stability marks → the standard library's `warnings.deprecated` (3.13+) plus a small stability package for `experimental`; lint and doctor read them, and a plugin using a removed name still fails at import;
- reading picks, profiles and settings → the host and the settings engine;
- cancellation → concurrency ([0037](0037-concurrency.md)); caching → an engine; printing → the host.

The kernel reads no files and no environment, keeps no files, and uses the standard library only.

## Consequences

- "A slot needed before plugins load gets its pick from the environment or core's own small file" ([0012](0012-two-engines-refuse.md)) becomes: the host reads it.
- "Core keeps its own small folder for picks and the startup cache" goes away.
- The startup declaration cache needs an owner (still open).
- K3 and K4 were reworked right after: the kernel's own extension points ([0027](0027-kernel-extension-points.md)), resolvers ([0029](0029-resolvers.md)) and `Dispatch` ([0028](0028-wrappers-leave-the-kernel.md)).
