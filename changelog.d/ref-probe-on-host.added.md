`untaped_git.api.probe_refs(urls, kinds=, default_branch_only=)`
(experimental) reads many repositories' branches and tags. It asks the plugin
that serves each host once for all of that host's URLs, through the optional
`GitHost.probe_refs` (experimental), and reads the rest with `git ls-remote`,
`git.probe_parallel` at once: URLs on a host with no probe, a URL that probe
could not read for any reason but "not found", and every URL of a host whose
probe was unavailable. It reports each host's budget. github fills the probe
with its batched GraphQL reads and says the budget is spent below
`github.ref_probe.rate_limit_floor`.
