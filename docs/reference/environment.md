# Environment variables

Every environment variable `untaped` reads or sets.

## untaped

| Variable | Effect |
|---|---|
| `UNTAPED_CONFIG` | Path of the config file. Default: `~/.untaped/config.yml`. See [File and layout](../configuration.md#file-and-layout). |
| `UNTAPED_STATE` | Path of the state file. Default: derived from the config file; see [File and layout](../configuration.md#file-and-layout). |
| `UNTAPED_PROFILE` | Active profile for this process. It must name an existing profile. The root `--profile` option takes precedence over it. |
| `UNTAPED_FORMAT` | Default `--format` (`json`, `yaml`, `table`, `raw` or `pipe`) for commands whose default is `table`. Wins over the `ui.format` setting; an explicit `--format` wins over it. Any other value exits 2, except under `doctor` and `setup`, which ignore it. |
| `UNTAPED_DIAGNOSTICS` | `json` makes stderr diagnostics (errors, warnings, hints, notes) JSON Lines for every format; `text` keeps text lines even with `--format json`, `yaml` or `pipe` (from the flag, `UNTAPED_FORMAT` or `ui.format`), which otherwise switch to JSON. Other values are ignored. See [stderr diagnostics](../scripting.md#stderr-diagnostics). |
| `UNTAPED_CONFIG_LOCK_TIMEOUT` | Seconds to wait for the config or state file lock before a write fails. A non-negative number; default `5`. |
| `UNTAPED_<SECTION>__<FIELD>` | Overrides one profile setting for this process, for example `UNTAPED_GITHUB__TOKEN` or `UNTAPED_HTTP__VERIFY_SSL`. Nested fields add another `__`: `UNTAPED_GITHUB__SWEEP__MAX_AGE_SECONDS`. |
| `GH_TOKEN`, then `GITHUB_TOKEN` | GitHub token used when neither `github.token` nor `github.token_command` is set (read as the GitHub CLI reads them). See [Tokens](../configuration.md#tokens). |
| `JIRA_API_TOKEN` | Jira token used when neither `jira.token` nor `jira.token_command` is set (read as `jira-cli` reads it). |
| `CONTROLLER_OAUTH_TOKEN`, then `TOWER_OAUTH_TOKEN`, then `AAP_TOKEN` | AWX/AAP token used when neither `awx.token` nor `awx.token_command` is set (read as the `ansible.controller` and `awx.awx` collections read them). |

A setting's source order is in
[File and layout](../configuration.md#file-and-layout). The [configuration reference](../reference/config.md) lists the variable for
every setting. `untaped doctor` names the variable when an override holds an
invalid value.

## Workspace run

`untaped workspace run` sets these in each repo's process.

| Variable | Value |
|---|---|
| `UNTAPED_WORKSPACE` | Workspace name. |
| `UNTAPED_REPO` | Repo display name. |
| `UNTAPED_BRANCH` | Task branch; empty for a read-only repo. |
| `UNTAPED_BASE` | Base branch. |
| `UNTAPED_READ_ONLY` | `1` for a read-only repo, else `0`. |

## Editors

| Variable | Used by |
|---|---|
| `VISUAL`, then `EDITOR` | `untaped config edit`, `untaped awx <resource> edit`, `untaped recipe edit` (and `recipe packs edit`, `recipe hooks edit`). |

The value is split like a shell command line but no shell runs it. Include your
GUI editor's wait flag, for example `VISUAL="code --wait"`. If neither is set,
the commands fail and ask you to set one.

## Terminal output

| Variable | Effect |
|---|---|
| `NO_COLOR` | Any non-empty value turns off color. It wins over `FORCE_COLOR`. |
| `FORCE_COLOR` | Any non-empty value turns on color even when output is not a terminal. |

Without either, color is used only when the stream is a terminal.
`COLUMNS` sets the table width; without it a terminal's width is used, and
output that does not go to a terminal is not wrapped.

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

Recipe hooks, and the `uv` commands `untaped` runs on a pack, get a reduced
environment: only an allowlist of variables passes through (`PATH`, `HOME`,
locale, temp directories, `UV_*`, `XDG_*`, TLS and proxy settings, and
`SSH_AUTH_SOCK`/`GIT_SSH_COMMAND`). Tokens such as `GITHUB_TOKEN` and
`UNTAPED_*` credentials are not passed. Hook workers get `PYTHONPATH` set to
the pack's `src/` only. The full list is in the recipe skill's
[pack library reference](../../packages/untaped-recipe/src/untaped_recipe/skills/untaped-recipe/references/library.md).
