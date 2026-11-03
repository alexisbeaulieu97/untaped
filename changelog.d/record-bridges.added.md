A contract can declare a record bridge, a method taking a `ProviderRecord`,
so a provider issues a second record of its own (a selector, say) whatever
its `T`; `own(item, cls)` reads it back typed, and `gather(plugins=)` answers
`not-filled` for a named plugin that doesn't fill an optional method.
