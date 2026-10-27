**Breaking (plugins):** `fills.json`, the owner schema hash `assert_fills`
wrote into it, the `plugin check` rows `fills` and `schema`, the
`owner-schema-drift` reason and lint's `lint.fills`, `lint.schema` and
`lint.owner-schema-drift` findings are gone. What guards a provider against an
owner that moved is the owner range in its `pyproject.toml`
(`owner-out-of-range`), validation of every answer as the owner's model, and
the owner's own surface snapshot.
