**Breaking (users):** `untaped dotfiles` is now built on a 12.0 plugin class.
Its state lives in `plugins/dotfiles/state.yml` and its lock in
`plugins/dotfiles/changing.lock`, beside the rest of its files, so
`dotfiles.state_dir` holds only `status.json` and `attention`. Under
`--dry-run`, `apply` and `remove` show their plan on stderr as they do before
asking (JSON info lines under `--format json`, where 11.x printed text);
`apply --dry-run` warns of a failed fetch, and exits 1 on a planned
`conflict` or `failed` row as a run does. The package and policy guides are
the docstrings of `PackageFile` and `Policy`, linked from the dotfiles
README.
