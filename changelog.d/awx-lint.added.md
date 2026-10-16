`untaped awx lint` (experimental) checks AWX resources against rules: apply
documents before you apply them (`FILES`, with references resolved in the
batch and then on the controller, or in the batch only with `--offline`), or
everything the controller holds, from a snapshot kept for an hour
(`awx.caches.resources.max_age_seconds`, `--refresh`/`--cached`). Ten rules
ship, from `awx.missing-reference` and `awx.ignored-launch-field` to
`awx.playbook-missing` and `awx.plaintext-secret`; a live finding carries
the `awx … patch` that fixes it. `--kind`, `--organization`, `--filter` and
`--search` narrow what is reported, and the ids, `--ignore`, `--select`,
`--strict` and exit codes are `untaped lint`'s. `untaped_awx.api` exports
the snapshot, its document views and the rules for a repository's or a
plugin's own rules, and `untaped_awx.testing.resources()` builds one from
YAML for their tests.
