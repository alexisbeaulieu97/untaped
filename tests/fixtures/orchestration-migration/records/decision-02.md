
Profile resolution and the profile management group (`create`/`list`/`use`/
`delete`), plus the built-in theme presets, are now part of the SDK and mounted
per tool by `run_tool`. The standalone `untaped-profile` and `untaped-themes`
packages are **retired** — a standalone tool would reintroduce "install one
more thing to manage shared state".
