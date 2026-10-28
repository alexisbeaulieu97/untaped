A 12.0 command that writes without asking previews through `dry_run: DryRun`:
core adds `--dry-run` alone (no `--yes`, no question, no "(asks first)" tag)
and the field is true after it. One command takes `Confirm` or `DryRun`, not
both. core's own `config set|unset|migrate`, `profile create|rename`,
`plugin contracts rank` and `plugin new` take it.
