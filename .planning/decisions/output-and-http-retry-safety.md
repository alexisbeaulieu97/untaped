# output and HTTP retry safety

Decision ID: `dec_01a0820d60a074ec98d9f6ad27a28508`

Untaped keeps the shared output and HTTP safety helpers as current
cross-capability contracts. `emit` renders one entity as a detail view and a
sequence as a collection; structured output follows that shape, while pipe
output retains the v1 envelope. `render_rows` remains available for explicit
row collections.

The default HTTP retry policy is conservative: pre-send transport failures may
retry for any method, post-send failures and `429`/`503` status retries require
an idempotent method, and `Retry-After` is bounded before exponential backoff.
Callers can pass a per-request retry policy when a specific operation is known
to be safe.

## Related decisions

- Supersedes: [dec_019f68b6b7f87484ae84f7b788f38138](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_019f68b6b7f87484ae84f7b788f38138-emit-detail-routing-and-safe-http-retries-sdk-2-1.md) (historical record)

Source: [preserved decision record](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_01a0820d60a074ec98d9f6ad27a28508-v4-output-and-http-retry-safety.md).
