**Breaking (users):** `untaped ansible` names a repository as its provider
lists it, so a nested name such as `group/sub/project` works in `deps`,
`impact`, `find` and `graph`, by name, by URL and in `NAME@ref` root lines,
and as a `source-alias set` target. The index keeps each repository's URL: a
dependency URL resolves when its host is one the source's own repositories
live on (`github.com` URLs no longer resolve from a source on another host),
and a graph target's URL also on the host of `github.server.url`. Source
removal releases each repository at its stored URL. The index moves to layout
6 (`index.v6.sqlite3`), so a 12.0 install refreshes its sources once.
