**Breaking (plugins):** `gather(method, plugins=, refresh=, progress=)`
loses `needs=` and `deadline=`, and `plugins=` takes provider classes as well
as plugin names. A provider's failed answer is `Errored` (was `Failed`), and
`Answers` has the views `.ok`, `.errored` and `.skipped`. A skip's or
error's `reason` is a `Reason` (`Reason.not_configured`), typed by its use;
the words on the wire are unchanged. `NotReady(reason, key(S).field)` takes
a typed settings key, which core spells in full as `NotReady.spelled`.
`Contract.http` is gone: a provider builds clients with
`self.ctx.http.client(...)`. A contract method may be named `http`,
`gather`, `convert`, `on` or `providers`.
