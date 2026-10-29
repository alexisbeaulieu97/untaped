# Environment variables

Every environment variable `untaped` reads or sets.

## untaped

| Variable | Effect |
|---|---|
| `UNTAPED_HOME` | The folder untaped keeps everything in: `config.yml` and `plugins/<name>/`. An absolute path (`~` is expanded); with a relative one, a command that reads the home exits 4 (`plugin list`), and one that never does still runs (`config --help`). Default: `~/.untaped`. See [File and layout](../configuration.md#file-and-layout). |
| `UNTAPED_PROFILE` | Profiles every run in this process layers over the base, a comma list in order; each must exist. The root `--profile` option's profiles stack after them, and `--profile default` sets them aside. Writes never go to them. |
| `UNTAPED_FORMAT` | Default `--format` (`json`, `yaml`, `table`, `raw` or `pipe`) for commands whose default is `table`. Wins over the `untaped.ui.format` setting; an explicit `--format` wins over it. Any other value exits 2 before the command runs, except under `doctor`, `skills`, `setup`, any command marked `repairs`, a `--help` line, a line naming no command and a command with another default or no `--format`, which ignore it. |
| `UNTAPED_DIAGNOSTICS` | `json` makes stderr diagnostics (errors, warnings, hints, info) JSON Lines for every format; `text` keeps text lines even with `--format json`, `yaml` or `pipe` (from the flag, `UNTAPED_FORMAT` or `untaped.ui.format`), which otherwise switch to JSON. Other values are ignored. See [stderr diagnostics](../scripting.md#stderr-diagnostics). |
| `UNTAPED_CONFIG_LOCK_TIMEOUT` | Seconds to wait for the `config.yml` lock before a write fails. A non-negative number; default `5`. |
| `UNTAPED_<SECTION>__<FIELD>` | Overrides one profile setting for this process, for example `UNTAPED_GITHUB__TOKEN` or `UNTAPED_UNTAPED__HTTP__TIMEOUT_SECONDS`. Nested fields add another `__`: `UNTAPED_GITHUB__SWEEP__PARALLEL`. A hyphen in a plugin name is an underscore: `UNTAPED_ACME_TOOLS__TOKEN`. |
| `GH_TOKEN`, then `GITHUB_TOKEN` | GitHub token used when no override variable, stored login or old `github.token` setting answers (read as the GitHub CLI reads them). See [Tokens](../configuration.md#tokens). |
| `JIRA_API_TOKEN` | Jira token used when no override variable, stored login or old `jira.token` setting answers (read as `jira-cli` reads it). |
| `CONTROLLER_OAUTH_TOKEN`, then `TOWER_OAUTH_TOKEN`, then `AAP_TOKEN` | AWX/AAP token used when no override variable, stored login or old `awx.token` setting answers (read as the `ansible.controller` and `awx.awx` collections read them). |

A setting's source order is in
[File and layout](../configuration.md#file-and-layout). The
[configuration reference](./config.md) lists the variable for every setting. `untaped doctor` names the variable when an override holds an
invalid value.

## Workspace run

`untaped workspace run` sets these in each repo's process.

| Variable | Value |
|---|---|
| `UNTAPED_WORKSPACE` | Workspace name. |
| `UNTAPED_REPO` | Repo display name. |
| `UNTAPED_BRANCH` | Task branch; empty for a read-only repo. |
| `UNTAPED_BASE` | Base branch or tag. |
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

When the `untaped.http.proxy` setting is unset, the HTTP client honors the standard
proxy variables: `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` and `NO_PROXY`. TLS
trust comes from the `untaped.http.*` settings (the OS trust store by default), not
from environment variables.

## Git

For every `git` call it makes, `untaped` does the following; Git you run
yourself, in a workspace checkout or anywhere else, is unaffected:

- runs Git in its own session, without the terminal, so nothing Git starts
  (ssh included) can prompt there: a key with a passphrase must be in
  `ssh-agent`, and a new host must be accepted once with your own
  `git fetch`, as the error's hint says;
- sets `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never`, so a remote that
  needs credentials fails instead of waiting for input, and
  `SSH_ASKPASS_REQUIRE=never` and an empty `GIT_ASKPASS` unless you set
  them, so neither ssh nor Git opens a graphical prompt instead (Git then
  skips `core.askPass` and `SSH_ASKPASS`; a credential helper or your own
  `GIT_ASKPASS` still supplies credentials);
- sets `GIT_SSH_COMMAND="ssh -o BatchMode=yes"` unless you set
  `GIT_SSH_COMMAND`, `GIT_SSH` or the `core.sshCommand` Git setting yourself;
- removes `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`,
  `GIT_ALTERNATE_OBJECT_DIRECTORIES` and `GIT_COMMON_DIR`, so an outer
  repository cannot redirect the command;
- removes `GIT_TRACE*` and `GIT_CURL_VERBOSE` when it passes a token to Git,
  so the token is not logged;
- finds `git` on `PATH` reading a relative entry (`.`) from the config
  file's folder, so a repository can't plant the `git` it runs.

If you set `GIT_SSH_COMMAND` yourself, add `-o BatchMode=yes` to keep the
fail-fast behavior. The git plugin's repo store also sets
`GIT_NO_LAZY_FETCH=1` on its local commands, so a file it never fetched fails
to read instead of being fetched one at a time.

When a Git command times out, `untaped` stops it and every process it
started: SIGTERM first, so Git removes its lock files, then SIGKILL two
seconds later. Ctrl-C, SIGTERM and SIGHUP sent to `untaped` (a closed
terminal, `timeout`, a cancelled CI job) are passed on to the Git commands it
is running.

## Recipe hooks

Recipe hooks, and the `uv` commands `untaped` runs on a pack, get only an
allowlist of environment variables: tokens such as `GITHUB_TOKEN` and
`UNTAPED_*` credentials are not passed. `untaped docs recipe guides hooks` lists
the variables that are.
