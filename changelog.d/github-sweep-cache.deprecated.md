`github.sweep.max_age_seconds` and `UNTAPED_GITHUB__SWEEP__MAX_AGE_SECONDS`
are deprecated in favour of `github.caches.sweep.max_age_seconds` and still
read, with a warning, until 12.0; `untaped config migrate` moves the key in
`config.yml`.
