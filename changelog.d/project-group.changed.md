`untaped project` (experimental) is now built on core's own plugin class.
`project list` prints `untaped.recorded_project` records (was
`untaped.project`); `move`, `remove` and `prune` ask through `Confirm`, so
without a terminal and `--yes` they exit 2 naming the question they ask.
Agent pages mark a command built on a plugin class `(asks first)` when it
takes `Confirm`, and give it neither `(changes things)` nor `(destructive)`:
`project move`, `remove` and `prune` say `(asks first)`, `project init` and
`plugin rank` and `new` carry no tag.
