**Breaking (users):** `untaped ansible` names a repository as its provider
lists it, so a nested name such as `group/sub/project` works in `deps`,
`impact`, `find` and `graph`, by name, by URL and in `NAME@ref` root lines,
and as a `source-alias set` target. The index keeps each repository's URL: a
URL in a requirements file or on the command line names a repository when
its host is one any refreshed source's repositories live on (a graph command
also takes the host of `github.server.url`), so `github.com` URLs no longer
resolve when no source lives there. A download URL (`.tar.gz`, `.zip`), a web
page and a template stay unresolved. Source removal releases each repository
at its stored URL. The index moves to layout 6 (`index.v6.sqlite3`), so a
12.0 install refreshes its sources once.
