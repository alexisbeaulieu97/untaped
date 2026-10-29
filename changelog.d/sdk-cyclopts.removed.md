**Breaking (plugins):** the cyclopts option and output helpers leave
`untaped.sdk`: `create_app`, the option aliases (`FormatOption`,
`TreeFormatOption`, `ColumnsOption`, `YesOption`, `DryRunOption`,
`StdinOption`, `ParallelOption`, `LimitOption`), `echo`, `emit`,
`emit_document`, `render_rows`, `check_columns`, `existing_file`,
`parse_kv_pairs`, `parse_json_pairs` and `clamp_parallel`. A plugin
class's fields declare what they did.
