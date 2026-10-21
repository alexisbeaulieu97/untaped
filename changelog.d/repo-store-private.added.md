git's `RepoStore.read_private` and `RepoStore.write_private` (experimental)
keep a plugin's typed file in a store repo, on the shared data-file format.
Workspace and ansible use them; adopting a repo with `setup migrate-dirs` now
copies another plugin's file whole, with only its freshness cleared.
