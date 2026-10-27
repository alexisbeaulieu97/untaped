In code that asks a contract, import `Errored` for `Failed`, use
`answers.ok`/`.errored`/`.skipped` for `isinstance` loops, compare reasons
with `Reason` members, read `NotReady.spelled` for the full key, and drop
`needs=` and `deadline=`. In a provider, return
`NotReady("why", key(MySettings).field)` for `setting="plugin.field"` and
replace `self.http` with a client from `self.ctx.http.client(self.settings,
...)`.
