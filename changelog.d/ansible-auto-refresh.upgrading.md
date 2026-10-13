The first 11.0 run takes each saved source's freshness from its last
`source refresh`, so nothing fresh is refreshed again. After that, scripts
that ran `impact` and friends on purpose against old data pass `--cached`, and
scripts that grepped for "source data is stale" read the `untaped.stale` line
instead. `ansible.stale_after_seconds` is now
`ansible.caches.index.max_age_seconds` (still read with a warning until 12.0,
as is `UNTAPED_ANSIBLE__STALE_AFTER_SECONDS`); the 9.x `ansible.stale_after`
is no longer read. `untaped config migrate` moves the first and deletes the
second.
