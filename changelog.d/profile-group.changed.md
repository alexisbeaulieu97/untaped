**Breaking (users):** `untaped profile` is now built on core's own plugin
class. `profile show` prints an `untaped.profile_view` record (`name`,
`selected`, `raw`, and the settings under `data`) in every output format,
YAML by default, instead of the bare settings and a `# profile:` line on
stderr. `profile list` keeps `untaped.profile`; `profile current` prints
`raw` by default as before. `profile delete` asks through `Confirm`, so
without a terminal and `--yes` it exits 2 naming the question it asks, and
agent pages mark it `(asks first)`; `profile create` and `rename` carry no
tag.
