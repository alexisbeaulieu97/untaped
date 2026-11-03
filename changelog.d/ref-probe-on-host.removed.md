**Breaking (users):** `--backend` leaves `ansible source refresh`, `deps`,
`impact`, `impact checkouts`, `impact uses`, `find` and `graph`, and the
settings `ansible.source_refresh_backend`, `ansible.probe_parallel` and
`ansible.source_refresh_rate_limit_floor` are deleted. A source refresh probes
each repository's refs through the plugin that serves its host and reads with
`git ls-remote` what that probe could not, in one warning grouped by reason.
It pauses once a host's probe budget is spent; the pause error belongs to
that host's plugin and its `retry_at` is when the budget resets.
