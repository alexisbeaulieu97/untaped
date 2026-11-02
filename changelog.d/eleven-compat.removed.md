**Breaking:** what 12.0 still read from 11.x: `config migrate` no longer
maps the setting names 11.x retired (`ansible.cache_dir`,
`ansible.repo_cache_path`, `ansible.stale_after`, the `ansible.git_*` and
`*_concurrency` settings, `awx.test_timeout`, `github.cache_dir`,
`github.corpus_path`, `github.inventory.path`,
`github.inventory.max_age_seconds`, `github.sweep.sync_concurrency`,
`recipe.library_root`); data files 11.x wrote without an `untaped` stamp
(a repo's private files, recipe backups, github corpus metadata, project
markers) are unreadable; an ansible index from before its schema stamp is
rebuilt; and a workspace's repo rows validate their name and URL like any
repo. For plugins, the experimental `read_data_file` loses `unstamped=`
and `legacy_stamps=`.
