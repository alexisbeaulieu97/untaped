**Breaking (cli):** A command that prints a failure exits non-zero even when
it finishes: the exit code is the most severe failure it printed or emitted
as a failed row, and a predicate hit (exit 3) no longer hides one.
