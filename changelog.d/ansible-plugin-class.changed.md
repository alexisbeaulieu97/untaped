**Breaking (users):** `untaped ansible` is now built on a 12.0 plugin class;
its record kinds keep their names. `ansible impact ROLE --uses NAME` is the
command `ansible impact uses ROLE NAME...`, with the same options. The index
lives in a folder: `ansible.index_dir` (unset: `plugins/ansible/index/` under
the home) holds `index.v<layout>.sqlite3`, and its cache is
`ansible.caches.source_data` (it was `ansible.caches.index`), so a stale line
names `ansible.source_data`. Sources and aliases are rows of
`plugins/ansible/state.yml`, an alias as `{alias, repo}`. A damaged row
doesn't stop `source remove` or `source-alias remove`, which delete it (a
damaged alias's outcome has no `repo`). `source patch`'s flags list under
their own help panel. A row piped into `find` must be of a kind an installed
plugin declares. `-c` with an unknown column is refused (exit 2) before
anything runs, as other tables' are; on `source-alias set` it only warned
after the change before. `ansible.index_path` and
`ansible.stale_after_seconds` are retired: they are no longer read, and
`untaped doctor` names them for `untaped config migrate` to delete. The
resolution and source-data guides are the docstrings of the plugin class and
of `SourceData`, on `untaped docs ansible` and at `untaped docs ansible guides
source-data`.
