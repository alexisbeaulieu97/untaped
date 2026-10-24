`Output(render={"raw": …})` replaces a core format with the plugin's own
renderer, and `Output.requested` tells `run` which `--columns` the run asked
for (`None` without the flag). Columns a command declares with
`Output(columns=…)` show in its table even when every row leaves them empty.
