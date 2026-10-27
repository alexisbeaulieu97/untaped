A script that ran `github sweep --show files` runs `github sweep files`
(`matches` likewise); one that passed `--no-fork` passes `--fork exclude`,
and a bare `--fork` becomes `--fork only`. Store the GitHub token with
`untaped auth login github`, or keep it in `GH_TOKEN`; point GitHub
Enterprise at `github.server.url`, and set a sweep's cache age with
`github.caches.sweep.max_age_seconds`. A script reading `fetched_at` from a
`cache status` or `sweep` row parses an ISO 8601 timestamp.
