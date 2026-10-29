`untaped-github` now requires `untaped-workspace`: it fills workspace's
`RepoSource` contract, so it imports workspace. Its `git` and `workspace`
extras, and `untaped-awx`'s `lint` extra, are gone, since each named a
dependency the plugin requires.
