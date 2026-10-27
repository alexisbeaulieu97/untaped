`Confirm.ensure_answerable(question)` refuses (exit 2) before a command does
the slow or side-effecting work its question follows, when nothing could
answer it: no terminal, no `--yes` and no `--dry-run`.
