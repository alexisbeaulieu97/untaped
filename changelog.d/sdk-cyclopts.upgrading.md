Declare a field block instead of each option alias: `output: Output[R]`
for `--format` and `--columns`, `confirm: Confirm` or `dry_run: DryRun`,
`stdin: Stdin[R]`, `parallel: Parallel(cap=...)` and `limit: Limit`. `run`
yields or returns its records instead of calling `emit` or `emit_document`,
and returns a string instead of calling `echo`; a tree is
`Output(render={"tree": ...})`. A `KeyValue` or `KeyJson` field replaces
`parse_kv_pairs` and `parse_json_pairs`, and `ExistingFile` replaces
`existing_file`.
