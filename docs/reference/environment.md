# Environment variables

These are all the environment variables `untaped` reads or sets.

## untaped

| Variable | Effect |
|---|---|
| `UNTAPED_CONFIG` | Path of the config file. Default: `~/.untaped/config.yml`. |
| `UNTAPED_STATE` | Path of the state file. Default: `state.yml` next to `config.yml`, or `NAME.state.yml` next to any other config file `NAME.EXT`. It must not name the config file. |
| `UNTAPED_PROFILE` | Active profile for this process. It must name an existing profile. The root `--profile` option takes precedence over it. |
| `UNTAPED_CONFIG_LOCK_TIMEOUT` | Seconds to wait for the config or state file lock before a write fails. A non-negative number; default `5`. |
| `UNTAPED_<SECTION>__<FIELD>` | Overrides one profile setting for this process, for example `UNTAPED_GITHUB__TOKEN` or `UNTAPED_HTTP__VERIFY_SSL`. Nested fields add another `__`: `UNTAPED_GITHUB__SWEEP__MAX_AGE_SECONDS`. |
| `UNTAPED_LOG_LEVEL` | Overrides the root `log_level` setting. |

A setting's value comes from the first of these that has it: its
`UNTAPED_*` variable, the active profile, `profiles.default`, the built-in
default. The [configuration reference](./config.md) lists the variable for
every setting. `untaped doctor` names the variable when an override holds an
invalid value.

## Editors

| Variable | Used by |
|---|---|
| `VISUAL`, then `EDITOR` | `untaped config edit`, `untaped awx <resource> edit`, `untaped recipe edit`, `untaped workspace edit`. |

The value is split like a shell command line but no shell runs it. Include your
GUI editor's wait flag, for example `VISUAL="code --wait"`. If neither is set,
`workspace edit` runs `vi`; the other commands fail and ask you to set one.
`workspace edit --editor CMD` overrides both.

## Terminal output

| Variable | Effect |
|---|---|
| `NO_COLOR` | Any non-empty value turns off color. It wins over `FORCE_COLOR`. |
| `FORCE_COLOR` | Any non-empty value turns on color even when output is not a terminal. |

Without either, color is used only when the stream is a terminal.

## HTTP

When the `http.proxy` setting is unset, the HTTP client honors the standard
proxy variables: `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` and `NO_PROXY`. TLS
trust comes from the `http.*` settings (the OS trust store by default), not
from environment variables.

## Git

`untaped` runs `git` for workspaces, GitHub sweeps and Ansible source refresh.
For each `git` call it:

- sets `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never`, so a remote that
  needs credentials fails instead of waiting for input;
- sets `GIT_SSH_COMMAND="ssh -o BatchMode=yes"` unless you set
  `GIT_SSH_COMMAND`, `GIT_SSH` or the `core.sshCommand` Git setting yourself;
- removes `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`,
  `GIT_ALTERNATE_OBJECT_DIRECTORIES` and `GIT_COMMON_DIR`, so an outer
  repository cannot redirect the command;
- removes `GIT_TRACE*` and `GIT_CURL_VERBOSE` when it passes a token to Git,
  so the token is not logged.

If you set `GIT_SSH_COMMAND` yourself, add `-o BatchMode=yes` to keep the
fail-fast behavior.

## Recipe hooks

Recipe hooks run in a separate `uv` environment. `untaped` removes
`VIRTUAL_ENV` from that environment and adds the pack's sources to
`PYTHONPATH`.

## See also

- [Configuration](../configuration.md)
- [Configuration reference](./config.md)
