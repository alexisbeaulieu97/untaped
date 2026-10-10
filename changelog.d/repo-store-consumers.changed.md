**Breaking (workspace, github):** workspace and github keep their
repositories in the git plugin's shared repo store under `git.store_dir`
(blobless, full history), and `github.cache_dir` and `workspace.cache_dir`
are gone. Other plugins' refs under `refs/untaped/` show in `git log --all`
from a workspace, and a local tag named like a remote tag follows the remote.
