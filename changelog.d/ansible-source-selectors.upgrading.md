Save each `untaped ansible` source again from its selector, such as
`untaped github repos selector --org acme --team acme/platform --format pipe
| untaped ansible source set platform --stdin`, then refresh it. A script
that passed `--org`, `--team` or `--repo` to `deps`, `impact`, `find` or
`graph` saves a source once and passes `--source NAME`. A script reading
`orgs`, `teams` or `repos` from `source list` reads `selector` instead.
