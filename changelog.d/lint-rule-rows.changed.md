**Breaking (plugin authors):** a function `@rule` declares carries its `id`,
`severity`, `summary` and `model`; its `Rule` row is built when lint loads its
provider, inside the run, so the kind it reads comes from composition. awx,
lint and the hello example name no `kind=` on their records; the kinds on the
wire are unchanged.
