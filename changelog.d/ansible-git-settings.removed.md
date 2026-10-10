**Breaking (ansible):** `ansible.cache_dir`, `ansible.git_fetch_depth`,
`ansible.git_blob_filter` and `ansible.git_clone_protocol` are gone: source
refreshes keep their repositories in the git plugin's repo store under
`git.store_dir` (blobless, full history) and clone with `github.git_protocol`.
A branch or tag whose name git allows but the store cannot hold (a leading
`-` or `+`, or `refs/`) is no longer indexed; `source refresh` names each one
in a warning.
