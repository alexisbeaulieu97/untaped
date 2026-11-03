A `merge` source's empty mapping (`a: {}`, `a = {}` or a bare `[a]`) no
longer takes over the target's keys under it: `untaped dotfiles remove`
leaves them, and editing them doesn't mark the entry changed.
