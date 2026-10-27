`fills.json`, the owner schema hash `assert_fills` wrote into it, the `plugin
check` and `untaped lint` rows `fills` and `schema`, and the
`owner-schema-drift` reason (`lint.fills`, `lint.schema`,
`lint.owner-schema-drift`) are gone. What guards a provider against an owner
that moved is the owner range in its `pyproject.toml` (`owner-out-of-range`),
validation of every answer as the owner's model, and the owner's own surface
snapshot.
