**Breaking (users, plugin authors):** the git plugin owns repo names: the
`RepoSource` contract, its `Repo` record and the lookup that turns a typed
name or URL into one repo moved from workspace to `untaped_git.api`, so a
plugin can name repos without depending on workspace. Piped repo rows are
`git.repo` (was `workspace.repo`), the answer cache's age is
`git.caches.repo_source_repos.max_age_seconds`, and providers are ranked with
`untaped plugin rank git.repo_source repos PLUGIN...`. Lookups, messages and
exit codes are otherwise unchanged.
