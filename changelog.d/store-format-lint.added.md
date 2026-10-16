A new `lint.store-format` rule (experimental, with the `lint` plugin) fails
where a plugin opens a database under its own directory without a `Store`:
`sqlite3.connect`, `shelve.open` or `dbm.open` called on a `plugin_dir(...)`
or `~/.untaped` path. It is an error, so a plugin's
`untaped_lint.testing.assert_clean` test may fail after the upgrade; open the
file through a `Store` (see
[caches.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/caches.md#stores)),
or, when every release of the major reads its layout, waive the call with
`# untaped: allow store-format`.
