**Breaking (users):** `untaped auth` is now built on core's own plugin class.
`auth login` and `auth forget` take a key field the plugin's settings don't
hold as `--key FIELD=VALUE` (repeated for several) instead of `--FIELD VALUE`.
`auth forget` and the deprecated `auth unset` ask through `Confirm`, as
before; the deprecated `auth migrate` gains `--yes`. Agent pages no longer tag
`auth login` and `auth set` `(changes things)` until 12.0 brings its write
tags. Commands, output and exit codes are otherwise unchanged.
