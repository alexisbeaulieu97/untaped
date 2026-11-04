Drop `--backend` from `untaped ansible` commands. Set `git.probe_parallel`
instead of `ansible.probe_parallel`, and `github.ref_probe.rate_limit_floor`
instead of `ansible.source_refresh_rate_limit_floor`; `untaped config migrate`
deletes the old keys. `deps --live` and the live half of `graph` read 8
repositories at once. There is no way to force `git ls-remote` for a host
whose plugin probes it: a repository that probe cannot read is read with
`git ls-remote` on its own.
