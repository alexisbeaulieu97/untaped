`repairs = True` on a 12.0 command class says it repairs untaped's setup: it
runs under an invalid `UNTAPED_FORMAT` and the per-run skills check skips it.
Core reads the mark and a command's preview (`--dry-run`, or
`Confirm.as_dry_run()` during `run`) from the declaration and its blocks,
never from a command's name or raw tokens. `doctor`, `doctor fix` and every
`skills` command carry it, so `skills` now also runs under an invalid
`UNTAPED_FORMAT`.
