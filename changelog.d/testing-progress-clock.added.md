`untaped.testing` freezes the progress clock during `invoke_cli`;
`progress_clock()` lets a test advance it and assert `untaped.progress`
lines.
