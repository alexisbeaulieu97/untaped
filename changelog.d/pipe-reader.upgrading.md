Save a run for `awx test run --compare` with `--format json`, and pipe the
rows an untaped command printed rather than hand-built ones. A plugin that
called `read_stdin_input` declares a `Stdin[R]` field and reads
`self.stdin.values()`, asking `self.stdin.kind(row)` for a row's kind; a
test that built a `PipeEnvelope` for `convert` passes a row of the
provider's record to `untaped.sdk.testing.piped_through`.
