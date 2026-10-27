A script that ran `ansible impact ROLE --uses NAME` runs `ansible impact
uses ROLE NAME` (each `--uses` value becomes one more NAME). Point
`ansible.index_dir` at a folder where you set `ansible.index_path`, and set
the cache age with `ansible.caches.source_data.max_age_seconds`. Saved
sources and aliases start empty in a 12.0 home: save them again with `source
set` and `source-alias set`, then `source refresh`.
