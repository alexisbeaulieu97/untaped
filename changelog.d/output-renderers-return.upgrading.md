A renderer that called `echo(text)` returns `text`; one that echoed several
lines returns them as a list; one that called `print_tree(graphs, ui=...)`
returns the graphs; one that printed `ui.styled(text)` returns the `Text`. A
renderer that printed exact content (`echo(text, nl=False)`) returns
`Verbatim(text)`.
