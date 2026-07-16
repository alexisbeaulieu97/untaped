# Architecture decisions

Canonical decision state lives in the repository's public orchestration store. Agents
should begin with `untaped-orchestration brief --format json` and use the CLI for any
further reads or guarded mutations.

The committed [decision view](../.untaped/orchestration/views/decisions.md) is generated,
human-readable output; it is not canonical agent input. The retained
[migration proof](orchestration-migration/coverage.toml) maps every byte of the former
free-form document to its disposition.
