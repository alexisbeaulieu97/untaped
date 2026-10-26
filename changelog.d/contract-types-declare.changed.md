**Breaking (plugins):** a contract method's types are its declaration.
`@bridge` and `@listing` are gone: a method returning a `list` is a listing,
whose invalid rows are dropped one by one, and the method taking `T` and
returning the owner's model is the bridge (a contract has at most one).
`@cached(max_age=..., label=...)` makes the answer cache, named
`<contract>_<method>` and owned by the plugin offering the contract; its
setting stays `<owner>.caches.<contract>_<method>.max_age_seconds`. `untaped lint` reports a
malformed rule as its own failed row and keeps the provider's other rules.
