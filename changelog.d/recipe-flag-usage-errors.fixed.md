`untaped recipe apply --recipe` without a pack path, and `recipe hooks run`
given a validate hook with `--file` or content, or a transform hook without
`--file`, exit 2 (usage); they exited 1. A validate hook given `--diff` now
names `--diff` in its refusal.
