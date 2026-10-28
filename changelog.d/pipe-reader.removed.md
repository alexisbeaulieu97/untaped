**Breaking (plugins):** `untaped.sdk.StdinInput`, `read_stdin_input` and
`Stdin.envelopes()` are gone, with the 11.x pipe reader. A command reads
piped rows with `Stdin[R]`: `values()`, `rows()` or `identifiers()`, each
row validated as its kind's model, strictly as JSON (the way
`Stdin(via=...)` already read a provider's row), so a string `id` is no
longer read as a number. `untaped.sdk.testing.piped_through(bridge, row)`
reads a provider's row as the owner's `Stdin(via=bridge)` does.
