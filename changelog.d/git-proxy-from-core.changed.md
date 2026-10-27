**Breaking (plugins):** `GitHost` keeps `home` and `credential`; its `proxy`
method and `HostAuth.proxy` are gone. The repo store asks core for
`untaped.http.proxy` itself, so Git fetches over HTTPS from every host go
through it, not only those from the GitHub host. Hints that name a command
quote it in backticks, and the `plugin rank` hint for a credential tie
starts with "run" like every other.
