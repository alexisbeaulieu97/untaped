**Breaking (github):** `github repos list` and `github search repos` write one
record, `github.repo`, with GitHub's own field names (`full_name`,
`html_url`, `clone_url`, `ssh_url`, …); `github.repo_hit` is gone, and
`github.sweep_repo` and `github.corpus_repo` rows carry the same repo fields.
`--stdin` reads `full_name` from all three.
