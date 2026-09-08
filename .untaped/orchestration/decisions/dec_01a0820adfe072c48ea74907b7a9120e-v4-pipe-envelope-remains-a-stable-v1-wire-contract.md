+++
schema = "untaped.orchestration.decision/v1"
id = "dec_01a0820adfe072c48ea74907b7a9120e"
kind = "decision"
title = "v4 pipe envelope remains a stable v1 wire contract"
created_at = "2026-09-08T17:24:18.538Z"
tags = []

[[links]]
relation = "supersedes"
target_store_id = "sto_019f68b6af9e721e970126ca31dbfde1"
target = "dec_019f68b6b4e475179664a514f3771211"
+++
Version 4 preserves the v1 `--format pipe` wire contract independently of the
application version. Pipe output is newline-delimited JSON with the
`"untaped": "1"` envelope marker, a capability-owned `kind`, and a `record`.
Composed commands consume this stream through the shared pipe helpers.

When a record identifies a concrete filesystem target, its
`record.target_path` is absolute and non-empty. Records whose kind ends in
`.summary` are informational and have no required target. Consumers use this
record contract rather than branching on another capability's domain fields.
