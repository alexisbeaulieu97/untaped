Scripts that read `profile show` read the settings under `data` (`yq .data`
or `jq .data`); the `# profile:` stderr line is gone, so read `name` and
`selected` from the record. A script that runs `profile delete` without a
terminal passes `--yes`, as before.
