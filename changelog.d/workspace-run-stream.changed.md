**Breaking (users):** `workspace run` prints each repo's row as it finishes,
so rows come in finishing order, with repos `--fail-fast` never started last.
`--format json` prints one object per line and `yaml` one document per row,
instead of one list at the end. The table is unchanged.
