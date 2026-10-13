`ansible.stale_after_seconds` and `UNTAPED_ANSIBLE__STALE_AFTER_SECONDS` are
still read, with a warning, until 12.0; `untaped config migrate` moves the
setting to `ansible.caches.index.max_age_seconds`.
