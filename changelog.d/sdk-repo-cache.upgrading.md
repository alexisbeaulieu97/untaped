Keep a plugin's repositories with `RepoStore.for_url(url, plugin=SPEC)` from
`untaped_git.api` (depend on `untaped-git`), which asks the host's `GitHost`
for credentials; `repo_url_parts` moved there too. Pass a credential header to
`run_git` as `auth_config={"http.<origin>/.extraHeader": header}`.
