If you still set `ansible.stale_after`, set
`ansible.caches.index.max_age_seconds` instead; `untaped config migrate`
deletes the old key and prints the value it held.
