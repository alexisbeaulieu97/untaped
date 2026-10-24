A script that runs `untaped config … --format json` and reads its stderr
parses one JSON object per line and reads `message` instead of matching a
`warning:` or `error:` prefix.
