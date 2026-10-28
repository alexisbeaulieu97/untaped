# untaped
untaped VERSION · ENV

Unified untaped developer CLI.

## Navigation

Every page's title is its address: `untaped docs`, `untaped docs PLUGIN`, `untaped docs PLUGIN SECTION`, `untaped docs PLUGIN SECTION NAME`. Drop the last word to go up a level. Core's own commands are the plugin `untaped`.

A plugin's sections are `commands` (what you can run), `inputs` (the files you write for its commands), `outputs` (the rows they print), `settings` and, for a plugin other plugins extend, `extensions`; its page lists the guides to read first (`untaped docs PLUGIN guides NAME`). Commands and guides are spelled as the command line spells them (`untaped docs untaped commands config set`), inputs and outputs as their kind spells itself (`untaped.config_migration_outcome` is `untaped docs untaped outputs config_migration_outcome`).

`untaped docs [ADDRESS] --search TEXT` prints every line of the pages under the address that contains the text, after its page's address.

## Plugins

### untaped
Core's commands: docs, config, profiles, projects, skills, doctor, setup, auth, plugins.

### acme
Manage widgets on racks.

### store
The providers store.
Extends acme `rack`: Where the widgets you can name in acme commands come from. (The widgets the store has in stock.)

## Shared by every command
- Settings live in `CONFIG`: each plugin's in its section at the top, and a named profile's under `profiles.<name>`. `untaped config` reads and changes them (keys are `<plugin>.<setting>`). A command missing a setting exits 4, and its hint names the other profiles that set it.
- Tokens are kept by `untaped auth`; a command missing one exits 4 and names the fix.
- Results are rows on stdout; progress and errors go to stderr, as JSON Lines under `--format json`. A command's own options, output formats and `--stdin` included where it has them, are on its page.
- Without a terminal nothing prompts. A command that needs an answer exits 2, and its error names what to pass, such as `--yes`.
- (asks first), on a command's page: it asks before it changes anything. `--yes`: Go ahead without asking. `--dry-run`: Show what would change and change nothing.
- Options every command takes:
  - `--profile`: Profiles to layer over the base for this invocation, comma-separated or repeated; stacked after UNTAPED_PROFILE's (`default` reads the base alone).
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
