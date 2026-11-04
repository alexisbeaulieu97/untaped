`untaped github repos selector --org/--team/--repo` prints a
`github.repo_selector` without listing anything or needing a token. Piped
where a `git.repo_selector` is read, it is kept as github's own record, and
`untaped_git.api.select_repos(plugin, selector)` (experimental) asks that
plugin for its repos through the optional `RepoSource.select` (experimental;
answers cached by `git.caches.repo_source_select`). A selector with only
`match` patterns, or one whose plugin doesn't fill `select`, takes the
plugin's `repos()` instead, and `match` narrows either answer.
`NotReady(methods=)` lets a provider name the contract methods
it can't answer yet; `gather`, `Stdin(via=)` and `untaped plugin check` ask it
for the others, and doctor names the waiting methods.
