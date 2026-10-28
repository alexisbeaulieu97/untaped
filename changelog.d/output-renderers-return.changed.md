**Breaking (plugins):** an `Output(render={...})` renderer returns what to
print, and core prints it in the run's theme. A string prints as it is, then a
newline, and a `Verbatim` string prints byte for byte with nothing added; a
Rich `Text` prints in colour where stdout supports it; a `TreeGraph` prints as
a themed tree, a line too wide for the terminal ending in an ellipsis. A list
prints each in order, and `None` prints nothing. A streamed output calls the
renderer per row and prints each return.
