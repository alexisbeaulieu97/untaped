**Breaking (plugins):** `@writes` and the lint rules that plugin classes
make moot: `missing-help`, `duplicate-option`, `empty-negative`,
`needless-negative`, `positional-or-keyword`, `undeclared-write`,
`mutation-format`, `destructive-controls`, `deprecated-option-panel` and
`mark-on-spec`; `wrong-deprecated` now checks settings only. Composition
checks what they did (every option has help, no flag answers twice, flags
follow the default, and now an argument can't be deprecated:
`deprecated-argument`), and `confirm: Confirm` gives a command `--yes` and
`--dry-run`. The `bad-skill-asset` and `duplicate-skill` quarantine reasons
are gone too: a plugin's skill is derived from its class.
