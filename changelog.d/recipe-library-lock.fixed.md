Two `untaped recipe` commands that change the pack library at once (two
`packs add`, or `packs add` while `sync` records a commit) no longer lose a
row of `packs.toml`: library writes now take a lock, and a command that waits
too long on another fails as busy with a hint to retry.
