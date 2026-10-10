A script that checked `status == "removed"` after `cache delete` also accepts
`released`: github no longer holds that repo, though another plugin still
does. Read worktree and repo paths from the rows rather than building them
from `github.cache_dir`.
