Rank repo providers again with `untaped plugin rank git.repo_source repos
PLUGIN...` and set a cache age under `git.caches.repo_source_repos`; a script
that filters piped rows on `workspace.repo` filters on `git.repo`. A plugin
filling `RepoSource` imports it, `Repo` and `RepoName` from `untaped_git.api`
and depends on `untaped-git` instead of `untaped-workspace`.
