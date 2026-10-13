`ansible.stale_after_seconds` is now `ansible.caches.index.max_age_seconds`,
and it decides when a graph command refreshes a source rather than only how
`source status` colours it. The 9.x `ansible.stale_after` is no longer read.
