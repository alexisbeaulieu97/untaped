**Breaking (scripts):** `github cache delete` and `cache prune` write
`github.cache_delete_outcome` rows (`full_name`, `ref`, `action`,
`disk_bytes`, `kept`, `path`): `action` is `deleted` (was `status: removed`),
`released`, `planned` under `--dry-run` (was `status: cached`) or `failed`
with `error`; a failed release used to print only a stderr line. A
`github.corpus_repo` row no longer has `kept` or `detail`.
