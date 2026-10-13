**Breaking (ansible):** `deps`, `impact`, `find` and `graph` refresh a
selected source before answering when it was never refreshed or is older
than `ansible.caches.index.max_age_seconds` (one day); `--cached` reads only
what is cached and `--refresh` refreshes now.
