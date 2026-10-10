**Breaking (github):** `--depth` on `github sweep` and `github cache sync` is
gone: the repo store keeps each repository's full history, blobless, and
fetches file contents only when a grep or a worktree reads them.
