github: `cache worktree --format raw` prints the worktree path, so
`$(untaped github cache worktree OWNER/NAME --format raw)` works (it printed
the repository name), and the record carries that directory as an absolute
`target_path`, the field other commands' records use for a path; `path`
stays.
([#540](https://github.com/alexisbeaulieu97/untaped/pull/540),
[#535](https://github.com/alexisbeaulieu97/untaped/issues/535),
[#561](https://github.com/alexisbeaulieu97/untaped/pull/561))
