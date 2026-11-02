A field of a pydantic model in a plugin's Python API can carry
`deprecated(removed_in=...)` in its annotation: the surface file dates it, and
a model that ignores unknown fields (`extra="ignore"`) refuses the mark, since
a caller's value would vanish silently once the field goes.
