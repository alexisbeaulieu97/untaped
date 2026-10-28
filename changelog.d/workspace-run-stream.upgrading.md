workspace: read `workspace run --format json` one object per line (`jq -s`
gathers a list) and sort by `dir` if a script relied on the workspace's order.
