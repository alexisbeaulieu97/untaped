**Breaking (plugins):** `untaped_github.api.GithubClient` is gone: a plugin
reaches GitHub through `github_api(settings)`, github's port (`GithubApi`),
served for the server's version.
