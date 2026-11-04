Run `untaped ansible source refresh` once per saved source: the index moves to
`index.v7.sqlite3` and starts empty. A requirements file that names a role by
a URL on a host no source's repositories live on (a public `github.com` role,
with every source on another host) now lists it as unresolved: map it with
`untaped ansible source-alias set NAME OWNER/REPO` to keep it in the graph.
