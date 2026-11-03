`untaped recipe apply --recipe` without a pack path, and `recipe hooks run`
given a validate hook with `--file`, content or `--diff`, or a transform hook
without `--file`, exit 2 (usage); they exited 1. The `--diff` refusal names
`--diff`.
