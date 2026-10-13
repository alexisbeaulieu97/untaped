`untaped ansible source status` reports each source's cache freshness
(`refreshed_at`, `max_age_seconds`, `partial`, `attempted_at`, `retry_at`,
`refresh_error`) beside what the index holds (`scanned_at` and the counts),
and its table shows `refreshed_at` instead of `scanned_at`. A source whose
last refresh found no refs is `fresh` with zero counts, not `not_refreshed`.
