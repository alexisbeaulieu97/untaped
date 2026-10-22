`github sweep` and `github cache sync` keep their repos' freshness like every
other cache: a sweep that scans a repo whose refresh failed, or an old copy
under `--cached`, says so in one `untaped.stale` line per repo, which replaces
the "refresh failed for N repos" and "stale REPO" warnings, and a repo whose
refresh failed is tried again after five minutes, or at once with `--refresh`.
