**Breaking (users):** `untaped auth` is now built on core's own plugin class.
`auth login` and `auth forget` take a key field the plugin's settings don't
hold as `--key FIELD=VALUE` (repeated for several fields, each once) instead
of `--FIELD VALUE`. `auth forget` and the deprecated `auth unset` ask through
`Confirm`, so off a terminal without `--yes` they exit 2 naming the question.
Agent pages tag `auth forget` `(asks first)` instead of `(destructive)`, and
no longer tag `auth login` and `auth set` `(changes things)` until 12.0
brings its write tags. A broken `untaped.ui` setting no longer fails `auth
list`, `status` or `migrate`. Commands, output and exit codes are otherwise
unchanged.
