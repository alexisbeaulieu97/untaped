# untaped
untaped VERSION · ENV

untaped is one command line built from plugins. Core (below) holds what every plugin shares: profiles, settings, tokens, output. Each plugin adds commands under `untaped PLUGIN`, and a plugin can offer extension points that other plugins fill.

Read a plugin's page before running its commands: `untaped agents PLUGIN`, then `untaped agents PLUGIN COMMAND` for a command's options and examples.

## Shared by every command
- Settings live in `CONFIG`: each plugin's under `profiles.<name>.<plugin>`. `untaped config` reads and changes them. A command missing a setting exits 4, and its hint names the other profiles that set it.
- Tokens are kept by `untaped auth`; a command missing one exits 4 and names the fix.
- Results are rows on stdout; progress and errors go to stderr, as JSON Lines under `--format json`. A command's own options, output formats and `--stdin` included where it has them, are on its page.
- Without a terminal nothing prompts. A command that needs an answer exits 2, and its error names what to pass, such as `--yes`.
- Options every command takes:
  - `--profile`: Override the active profile for this invocation only (scoped to the invocation; does not mutate UNTAPED_PROFILE).
  - `--verbose`, `-v`: Stream underlying tool output live and enable debug logging.
  - `--quiet`, `-q`: Suppress progress and success/info messages (errors still print).
- Exit codes:
  - 0 Success
  - 1 Runtime failure, a failed item, or a declined confirmation
  - 2 Bad invocation, detected before any side effect
  - 3 A predicate hit: `--check` drift, `--fail-on-match`, `--strict`
  - 4 The environment needs fixing: config, credentials, permissions
  - 5 A temporary failure (network, timeout, 5xx, 429, a held lock); retry later
  - 130 Interrupted (Ctrl-C), including at a prompt
  - A run that sees several failures exits with the most severe: 130 > 2 > 4 > 5 > 1 > 3 > 0

## untaped (core) · details: `untaped agents untaped`
- `config`: Inspect and modify `~/.untaped/config.yml`.
- `profile`: Manage configuration profiles in `~/.untaped/config.yml`.
- `project` [experimental]: Make a folder a project and list the projects this home records.
- `skills`: List, install, update, and remove agent skills shipped by untaped.
- `doctor`: Check the health of the shell and every composed plugin.
- `setup`: Configure a profile's services interactively.
- `auth`: Log in to the servers plugins talk to; keep tokens safe.
- `plugin`: Inspect installed plugins and their contracts.

## acme · details: `untaped agents acme`
Manage widgets.

## bin · details: `untaped agents bin`
- Extends: rack

## rack · details: `untaped agents rack`
- Where items come from.
  - **bin**.
