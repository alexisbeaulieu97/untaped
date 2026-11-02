A field of a pydantic model in a plugin's Python API can carry
`deprecated(removed_in=...)` in its annotation: the surface file dates it. A
model that keeps unknown fields (`extra="allow"`) may drop the field when
due; one that refuses them (`extra="forbid"`) keeps it until the model goes,
and one that ignores them (`extra="ignore"`) refuses the mark, since a
caller's value would vanish silently once the field goes.
