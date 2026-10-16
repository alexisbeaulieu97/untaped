`untaped dotfiles apply` and `dotfiles remove` show the plan they ask about
as unstyled, aligned text on stderr, now with each path's target, instead of
a table or the `--format` output; a column empty on every row is always left
out. The records on stdout are unchanged.
