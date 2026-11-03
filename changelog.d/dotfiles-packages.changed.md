**Breaking (users):** `untaped dotfiles` reads packages: each
`.untaped/dotfiles/<name>.yml` file of a source (a git repo or a local
folder) is one package of named items, with its own format `version`,
`{{ input }}` targets and per-OS and per-tag filters, checked when it loads;
the `dotfiles.yml` manifest is no longer read. The machine's sources, the
packages it turned on, its policies and its input answers live in
`config.yml` under `dotfiles.sources`, so copying that file to a new
machine and running `dotfiles apply` clones each source and places what is
on. The commands are `subscribe`, `unsubscribe`, `show`, `add`, `remove`,
`apply` and `status`, taking `PKG`, `PKG/ITEM`, `SOURCE/PKG` or
`SOURCE/PKG/ITEM`; `items`, `enable`, `disable`, `repos`, `sync`, `summary`
and `diff` are gone. A git source's clone is a worktree of the git plugin's
repo store under `dotfiles.clones_dir` (it was `dotfiles.repos_dir`), so the
git plugin's credentials and proxy apply. `apply` fetches and moves each
clone itself. The records are `dotfiles.package`, `dotfiles.package_detail`,
`dotfiles.entry_status` and `dotfiles.apply_outcome`.
