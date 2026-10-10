Install untaped-github beside untaped-workspace (`untaped[workspace,github]`,
or `untaped[all]`) to keep looking repos up by name. Move a
`workspace.protocol` value to `github.git_protocol` (`untaped config migrate`
names it), then run `untaped workspace repos resolve NAME` for each workspace
to save the new URLs. Replace local paths given to `--repo` with the repo's
clone URL, and pipe records with their kind (`--format pipe`).
