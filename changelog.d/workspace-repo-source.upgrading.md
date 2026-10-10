Install untaped-github beside untaped-workspace (`untaped[workspace,github]`,
or `untaped[all]`) to keep looking repos up by name. Move a
`workspace.protocol` value to `github.git_protocol` (`untaped config migrate`
names it); workspaces made before 11.0 keep the URLs they were made with,
and `untaped workspace repos resolve NAME` updates those made since. Replace
local paths given to `--repo` with the repo's clone URL, and pipe records
with their kind (`--format pipe`). Workspaces already holding a local-path
or `http://` repo still list, run, archive and remove; to add such a repo
again, give its clone URL.
