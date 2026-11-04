**Breaking (users):** the inline sources leave `ansible deps`, `impact`,
`impact uses`, `impact checkouts`, `find` and `graph` (`--org`, `--team`,
`--repo`, `--path`, `--ref-kind`, `--ref-pattern`, `--ref-scan-default`): save
a source and pass `--source`. `ansible source set` loses `--org`, `--team` and
`--repo`, and `source patch` loses `--add-`, `--remove-` and `--clear-` of
`org`, `team` and `repo`; it still changes paths and ref selectors.
