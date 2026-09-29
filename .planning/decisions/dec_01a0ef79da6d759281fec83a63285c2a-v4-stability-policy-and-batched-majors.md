# stability policy: documented contracts, experimental surfaces, batched majors

Decision ID: `dec_01a0ef79da6d759281fec83a63285c2a`

Versions 1.0 to 8.x shipped a major release for each breaking change, four
of them in three weeks. That history is public and immutable on PyPI, so it
stays; resetting to 0.x would need yanking every release and would make
versions sort backwards. Instead, from 9.0.0 on:

- `docs/stability.md` lists what a major release keeps compatible (command
  and flag names, exit codes, record kinds and fields, config and state
  settings, environment variables, the provider SDK) and what it does not
  (human-readable output, experimental surfaces, undocumented internals).
- Surfaces still being shaped are marked experimental in `--help` and their
  guide, and may break in a minor release. This replaces the freedom a 0.x
  version would give.
- Renames go through `deprecated_alias`; other breaking changes wait for the
  next major, and are released together with an upgrade section.

## Related decisions

- Builds on: [unified distribution and CLI version identity](dec_01a0820d62a473538c658510093a134c-v4-unified-distribution-and-cli-version-identity.md)
- Builds on: [pipe envelope remains a stable v1 wire contract](dec_01a0820adfe072c48ea74907b7a9120e-v4-pipe-envelope-remains-a-stable-v1-wire-contract.md)
