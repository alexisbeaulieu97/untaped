**Breaking (sdk):** `untaped.sdk` no longer exports `RepoCache`, `cache_key`,
`cache_path`, `cache_origin`, `list_caches`, `repo_url_parts`,
`scoped_auth_header` and `git_auth_header`, and `run_git` takes
`auth_config=` instead of `auth_header=` and `auth_url=`.
