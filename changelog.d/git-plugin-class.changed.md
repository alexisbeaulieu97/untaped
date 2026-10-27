**Breaking (users):** `untaped git` is now built on a 12.0 plugin class.
`git store`'s record kind takes its class name, `git.store_report` (was
`git.store`); `git hosts` still writes `git.host`. `git credential` answers
nothing, with a warning, for a helper line without `--nonce`: the helper
lines 11.0 wrote are gone with the 11.x home, and the
`git.nonceless-credential-helper` behaviour is removed.
