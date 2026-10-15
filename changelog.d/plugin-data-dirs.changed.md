**Breaking (ansible, recipe, dotfiles):** each plugin keeps its own data
under `~/.untaped/plugins/<name>/`: the ansible index
(`ansible.index_path`), the recipe library (`recipe.library_dir`) and the
dotfiles directories (`dotfiles.repos_dir`, `kept_dir` and `state_dir`).
`untaped setup migrate-dirs` moves the 10.x data there while those settings
keep their defaults, and repoints dotfiles' links and saved repo paths.
