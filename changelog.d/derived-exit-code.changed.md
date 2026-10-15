**Breaking (core):** a command that prints a failure exits non-zero even when
it finishes: the exit code is the most severe failure it printed or emitted as
a failed row, and a predicate hit (exit 3) no longer hides one. Among shipped
commands this changes only three dry runs, which now fail on the problem their
real run fails on: `dotfiles apply --dry-run` (an item gone from the manifest,
a failed fetch, a placed path it cannot read), `dotfiles remove --dry-run` (an
item gone from the manifest) and `doctor fix --dry-run` (a fix that cannot
run). ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
