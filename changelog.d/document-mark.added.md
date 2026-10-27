Plugin authors can mark an option that names a document the plugin finds
and reads itself (a library name or a path) with `Document(M)`:
`Annotated[Positional[str], Document(RecipeFile)]`. Core reads nothing and
checks nothing, but lists `M` among the command's inputs, as it does for a
`File[M]` option.
