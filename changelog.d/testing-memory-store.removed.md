**Breaking (plugins):** the testing kit's `MemoryStore`, `memory_store()`
and `current_memory_store()` are gone. Every test runs with a
`MemoryCredentials`, which the `credentials` fixture hands over.
