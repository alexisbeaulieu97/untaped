A script that ran `ansible impact ROLE --uses NAME` runs `ansible impact uses
ROLE NAME` (each `--uses` value becomes one more NAME). Where you set
`ansible.index_path`, set `ansible.index_dir` to the folder that held it: an
index named `index.sqlite3` there is read again, any other name is not, and
`source refresh` builds a new one. Set the cache age with
`ansible.caches.source_data.max_age_seconds`. Saved sources and aliases start
empty in a 12.0 home: save them again with `source set` and `source-alias
set`, then `source refresh`. A script reading `source-alias remove` rows finds
no `repo` on the row of a damaged alias.
