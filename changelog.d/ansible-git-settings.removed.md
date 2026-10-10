**Breaking (ansible):** `ansible.cache_dir`, `ansible.git_fetch_depth`,
`ansible.git_blob_filter` and `ansible.git_clone_protocol` are gone: source
refreshes keep their repositories in the git plugin's repo store under
`git.store_dir` (blobless, full history) and clone with `github.git_protocol`.
