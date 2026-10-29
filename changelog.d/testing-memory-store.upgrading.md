A plugin test that read `current_memory_store()` takes the `credentials`
fixture instead (`def test_login(credentials: MemoryCredentials)`), and reads
or files secrets through it (`credentials.entries`, or
`credentials.set(Hello.token, HelloServer(...), "secret")` after
`install(Hello)`). A test that installed its own store with `memory_store()`
loads `untaped.sdk.testing.plugin` instead
(`pytest_plugins = ["untaped.sdk.testing.plugin"]` in its top-level
`conftest.py`) and uses the store it installs for every test.
