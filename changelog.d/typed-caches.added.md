A plugin's cache can keep the data itself: `Cache(..., model=Snapshot)` stores
what each fill returns as `Filled(value=...)`, with its freshness, in one file
per version of the value, serves another version's data as outdated when a
refresh fails or under `--cached`, and keeps a credential's data apart with
`serve(partition=)`. `read_data_file`, `write_data_file` and `copy_data_file`
(experimental) give any other file a plugin keeps one stamped, owner-only
format that tells a damaged file from a missing one
([caches.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/caches.md#a-cache-that-keeps-its-data)).
