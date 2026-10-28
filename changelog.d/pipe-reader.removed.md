**Breaking (plugins):** `untaped.sdk.StdinInput` and `read_stdin_input` are
gone with the 11.x pipe reader (see contract-envelope for `PipeEnvelope` and
`convert`). A command reads piped rows with a `Stdin[R]` field: `values()`,
`rows()` or `identifiers()`, each row validated whole as its kind's model,
strictly as JSON. `untaped.sdk.testing.piped_through(bridge, row)` reads a
provider's row as the owner's `Stdin(via=bridge)` does.
