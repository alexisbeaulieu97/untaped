Save a run for `awx test run --compare` with `--format json`. A plugin that
read `Stdin.envelopes()` reads `values()` and asks `stdin.kind(row)` for a
row's kind; a test that built a `PipeEnvelope` for `convert` passes a row of
the provider's record to `untaped.sdk.testing.piped_through`.
