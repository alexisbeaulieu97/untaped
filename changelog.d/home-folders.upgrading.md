Workspaces are under `plugins/workspace/checkouts/` in the home, not
`~/.untaped/workspaces`; set `workspace.workspaces_dir` to keep them in
another folder. A shell prompt segment reads
`plugins/dotfiles/prompt/attention` (and `status.json`) in the home, or the
folder `dotfiles.state_dir` names. These folder settings take an absolute
path (`~` is expanded); a relative one is a config error (exit 4).
