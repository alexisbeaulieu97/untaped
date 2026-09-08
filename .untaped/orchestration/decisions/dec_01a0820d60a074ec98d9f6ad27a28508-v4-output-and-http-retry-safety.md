+++
schema = "untaped.orchestration.decision/v1"
id = "dec_01a0820d60a074ec98d9f6ad27a28508"
kind = "decision"
title = "v4 output and HTTP retry safety"
created_at = "2026-09-08T17:25:32.576Z"
tags = []

[[links]]
relation = "supersedes"
target_store_id = "sto_019f68b6af9e721e970126ca31dbfde1"
target = "dec_019f68b6b7f87484ae84f7b788f38138"
+++
Version 4 keeps the shared output and HTTP safety helpers as current
cross-capability contracts. `emit` renders one entity as a detail view and a
sequence as a collection; structured output follows that shape, while pipe
output retains the v1 envelope. `render_rows` remains available for explicit
row collections.

The default HTTP retry policy is conservative: pre-send transport failures may
retry for any method, post-send failures and `429`/`503` status retries require
an idempotent method, and `Retry-After` is bounded before exponential backoff.
Callers can pass a per-request retry policy when a specific operation is known
to be safe.
