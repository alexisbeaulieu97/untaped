A plugin that built `GithubClient(settings, http=...)` calls
`untaped_github.api.github_api(settings)` instead, with the same methods and
no `with` block: the run closes its connections.
