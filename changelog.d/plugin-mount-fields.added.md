In a 12.0 command's `run`, `self.refresh.directive` says what `--refresh` or
`--cached` asked, `self.parallel.value` and `self.limit.value` give the
numbers (`Parallel`'s from its setting when `-j` is not given, clamped to its
cap with a warning), and `self.confirm(question, preview=..., needed=...)`
asks once before the command commits: `--yes` goes ahead, `--dry-run` previews
and returns `False`, and without a terminal it refuses with `NeedsYesError`
(exit 2) naming the command and its question.
