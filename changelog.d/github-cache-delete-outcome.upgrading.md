A script reading `github cache delete` or `cache prune` rows reads `action`
(`deleted`, `released`, `planned` or `failed`) where it read `status`
(`removed`, `cached`), and finds a failed release as a `failed` row with
`error`. A script reading `github.corpus_repo` rows finds no `kept` or
`detail`.
