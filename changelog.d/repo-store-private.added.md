git's `RepoStore.read_private` and `RepoStore.write_private` (experimental)
keep a plugin's typed file in a store repo, on the shared data-file format.
github, workspace and ansible use them. github now keeps when it last fetched
a repo in the file's stamp, so each repo github's sweep had fetched under 11.0
is fetched once more after upgrading (only what changed downloads), and a
metadata file it can't read is refetched instead of leaving the repo
unscanned. Adopting a repo with `setup migrate-dirs` copies another plugin's
file whole, with only its freshness cleared.
