# pipe envelope remains a stable v1 wire contract

Decision ID: `dec_01a0820adfe072c48ea74907b7a9120e`

Untaped preserves the v1 `--format pipe` wire contract independently of the
application version. Pipe output is newline-delimited JSON with the
`"untaped": "1"` envelope marker, a capability-owned `kind`, and a `record`.
Composed commands consume this stream through the shared pipe helpers.

When a record identifies a concrete filesystem target, its
`record.target_path` is absolute and non-empty. Records whose kind ends in
`.summary` are informational and have no required target. Consumers use this
record contract rather than branching on another capability's domain fields.

## Related decisions

- Supersedes: [dec_019f68b6b4e475179664a514f3771211](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_019f68b6b4e475179664a514f3771211-pipe-envelope-v1-versioned-independently-of-the-sdk.md) (historical record)

Source: [preserved decision record](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_01a0820adfe072c48ea74907b7a9120e-v4-pipe-envelope-remains-a-stable-v1-wire-contract.md).
