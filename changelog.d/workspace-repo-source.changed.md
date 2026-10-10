**Breaking (workspace):** workspace asks every installed plugin that fills
its `RepoSource` contract for repos, instead of reading GitHub's inventory,
and no longer depends on untaped-github. A name no provider lists exits 2
(the guess of a GitHub URL from the name is gone); a typed URL must be
`https://`, `ssh://` or `user@host:path` and its repo is named by its whole
path; `--stdin` records need a kind a provider reads; `workspace.protocol`
is retired in favour of each provider's setting (`github.git_protocol`).
