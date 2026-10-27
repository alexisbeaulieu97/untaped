**Breaking (users):** `untaped dotfiles` is now built on a 12.0 plugin class.
`dotfiles status --summary` is the command `dotfiles summary`. Its record
kinds take their class names: `dotfiles.path_status` (was `dotfiles.status`)
and `dotfiles.status_summary` (was `dotfiles.status.summary`), and
`status.json` holds the latter. `unsubscribe` prints its one row as a record
rather than a list. Its state lives in `plugins/dotfiles/state.yml` and its
lock in `plugins/dotfiles/sync.lock`, beside the rest of its files, so
`dotfiles.state_dir` holds only `status.json` and `attention`. Under
`--dry-run`, `apply` and `remove` show their plan on stderr as they do before
asking (JSON info lines under `--format json`, where 11.x printed text);
`apply --dry-run` warns of a failed fetch, and exits 1 on a planned
`conflict` or `failed` row as a run does. The manifest and policy guides are
the docstrings of `Manifest` and `Policy` now, linked from the dotfiles
README; the policies page is `untaped docs dotfiles guides policy`.
