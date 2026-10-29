Replace `--org` with `--organization` and `--inventory-org` with
`--inventory-organization`, `export --print-paths` with `export --format
paths`, and `awx schema --format yaml` with `awx schema --yaml`. A script
that matched a renamed record kind (`awx.job`, `awx.event`, `awx.log`,
`awx.status`, `awx.test_result`, ...) matches its new name. A script that
read `jobs events -f json` or `jobs logs -f json` without `--follow` as one
array reads one object per line. A script that read several columns from
`-f raw` names them with `--columns`. A script that read a lone document
from a kind's `export -f json` reads the first item of the list. A script
that passed `--format` with `export --out -` drops it (stdout is YAML). A
script that read a `usage` row's `target` as an id reads `target.id`. A rule
module that imported `untaped_awx.api.AwxResources` imports
`untaped_awx.api.Resources`. Where a guide was read by name, read the new
address in the changed entry.
