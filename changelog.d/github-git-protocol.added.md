`github.git_protocol` (`https` or `ssh`) sets how `github sweep` and
`github cache sync` fetch repositories on the GitHub host; with `ssh` they
use `git@HOST:OWNER/NAME.git` and your own keys. `http.proxy` now covers
those fetches from the GitHub host too.
