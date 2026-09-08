
Two additive surface changes in SDK 2.1, both about making the common case
correct by default.

**`emit` routes a single entity to a detail view.** Single-entity commands
(`whoami`/`get`/`show`/`status`) used to render their one object as a one-row
table via `echo(render_rows([x.model_dump()], …))` — a wide, awkward shape, easy
to forget the `echo` (silent no output), and a manual `model_dump()`. `emit`
dispatches by shape: a single pydantic model or `Mapping` renders as a vertical
`key: value` **detail** view (reviving the previously-unused
`UiContext.detail()`), while a sequence renders as a **collection**. It accepts
models directly and writes stdout itself. Under structured formats this also
fixes the single-entity JSON shape: a tool that adopts `emit` emits a bare
object `{…}` instead of a one-element array `[{…}]` for a single entity.
`--format pipe` is unchanged (the per-record envelope is identical to
`render_rows`), so the pipe contract from #3 is untouched. `render_rows` stays
for explicit row collections.

**`connected_client` retries transient failures by default.** A new public
`RetryPolicy` (a frozen dataclass) backs retries in `HttpClient`, and
`connected_client(...)` enables a safe default `RetryPolicy()` automatically
(`retry=None` disables it; a custom policy overrides). The policy is deliberately
conservative so an automatic default can never silently double a non-idempotent
write:

- **Transport failures are phase-gated.** A *pre-send* connect failure never
  reached the server, so it is retried for any method; a *post-send* read/write
  error may already have been processed, so it is retried only for idempotent
  methods. This distinction is what makes retrying-by-default safe.
- **`429`/`503` retries are idempotency-gated.** They apply only to
  `idempotent_methods` (`GET`/`HEAD`/`OPTIONS`/`PUT`/`DELETE` by default). A
  caller whose `POST` is genuinely idempotent (a search endpoint) opts in by
  passing a `RetryPolicy` whose `idempotent_methods` includes `"POST"`.
- `Retry-After` (seconds or HTTP-date) is honored up to `retry_after_max`;
  otherwise the delay is exponential backoff capped at `backoff_max`.

**`paginate_offset` forwards a per-call `retry=` (2.2.0).** The POST opt-in above
only reaches a single request; collection walks go through `paginate_offset`,
which previously fetched each page with the client's default policy and no way to
override it. It now takes a `retry=` (default `_INHERIT`) and forwards it to every
page fetch, so a tool can make just its idempotent search endpoint retry without
making any other `POST` (e.g. a create) retryable. Additive; existing callers that
omit `retry=` are unchanged.
