`untaped.sdk.testing.install` takes `providers={"fake": (FakeBooks,)}`, which
composes the provider classes a test defines as a plugin named `fake` (its
settings are the providers' `Provider[S]`, so `settings=[S(...)]` reaches
them), and composes the owners an installed provider requires, so
`install(Library)` brings `shelf`, whose section `settings=` can
write too.
