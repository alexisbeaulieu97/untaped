**Breaking (ansible):** `deps`, `impact`, `find` and `graph` refresh a
selected source before answering when it was never refreshed or is older
than `ansible.caches.index.max_age_seconds` (one day); `--cached` reads only
what is cached and `--refresh` refreshes now. A refresh that fails over cached
data serves it with one `untaped.stale` warning, which replaces the old
"source data is stale" and "refresh of NAME had N failures" warnings, and an
empty answer from a source that could not be refreshed exits with the
refresh's error. `--backend` applies to whichever refresh runs and is an error
only with `--cached`.
