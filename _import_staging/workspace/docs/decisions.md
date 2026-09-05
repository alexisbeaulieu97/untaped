# Architecture decisions

Canonical decision state lives in this repository's initially empty, public,
decision-only orchestration store. Use `untaped-orchestration` for canonical reads and
revision guard protected mutations; agents must never use `--force-current`.

The committed [decision view](../.untaped/orchestration/views/decisions.md) is generated
human-readable output and never tool input. Validate with `check --local`,
`fmt --check --local`, and `render --check`. Recover through `check` and `render`, not
hand edits.
