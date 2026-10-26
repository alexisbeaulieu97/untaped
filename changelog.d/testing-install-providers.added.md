`untaped.sdk.testing.install` takes `providers={"fake": (FakeBooks,)}`, which
composes the provider classes a test defines as a plugin named `fake`, and
composes the owners an installed provider declares under their extras, so
`install(Library)` brings `shelf`.
