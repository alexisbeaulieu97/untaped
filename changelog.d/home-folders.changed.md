**Breaking (users):** the folders plugins keep follow the untaped home
(`UNTAPED_HOME`) when their setting is unset: the repo store (`git.store_dir`)
is `plugins/git/store/` under the home, workspaces
(`workspace.workspaces_dir`) are under `plugins/workspace/checkouts/`, and
dotfiles' clones, kept files and prompt files (`dotfiles.repos_dir`,
`kept_dir`, `state_dir`) are under `plugins/dotfiles/clones/`, `kept/` and
`prompt/`. Workspaces were under `~/.untaped/workspaces` and a prompt segment
read `~/.untaped/plugins/dotfiles/attention`; set the setting, an absolute
path, to keep a folder elsewhere.
