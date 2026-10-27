A script that ran `ansible impact ROLE --uses NAME` runs `ansible impact uses
ROLE NAME` (each `--uses` value becomes one more NAME). Where you set
`ansible.index_path`, set `ansible.index_dir` to a folder instead: the old
file is not reused, and `source refresh` builds the new one. Set the cache age
with `ansible.caches.source_data.max_age_seconds`. Saved sources and aliases
start empty in a 12.0 home: save them again with `source set` and
`source-alias set`, then `source refresh`.
