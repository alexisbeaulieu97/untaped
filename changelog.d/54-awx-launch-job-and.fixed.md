awx: each target a command fails or leaves undone now gets an attributed
stderr line, where it got none or an `info` one: `error: <item>: …` for a
failed request, write (`apply`, `patch`, `edit`, a membership `add` or
`remove`) or `delete`, and for a refused `jobs cancel` or `jobs relaunch`
target; `warning: <item>: …` for a `launch` or `sync` job that failed, timed
out or was skipped, an execution Ctrl-C leaves behind, a `jobs wait`
timeout, the kinds a bulk `export` skips, the finished executions
`jobs cancel` skips, the targets a failed `delete` stopped, and a write
skipped or stopped by a conflict. An invalid `edit` batch and `test`'s
unknown-launch-field warning are `warning` lines too, and the hint after
Ctrl-C reads ``hint: run `untaped awx jobs wait …` ``.
([#464](https://github.com/alexisbeaulieu97/untaped/pull/464),
[#488](https://github.com/alexisbeaulieu97/untaped/pull/488),
[#491](https://github.com/alexisbeaulieu97/untaped/pull/491),
[#494](https://github.com/alexisbeaulieu97/untaped/pull/494),
[#496](https://github.com/alexisbeaulieu97/untaped/pull/496),
[#561](https://github.com/alexisbeaulieu97/untaped/pull/561))
