`untaped.sdk.contracts.providers(Contract)` (experimental) lists every offer
to fill a contract: each provider class with its own record and that
record's kind, or why it was refused. An owner whose files name a provider
by its record's kind finds the provider and asks it alone with
`gather(..., plugins={offer.provider})`.
An extension point's docs page (`untaped docs PLUGIN extensions NAME`) lists
the records its providers issue, with their fields.
A plugin may fill one contract with several providers when each issues a
record of its own; each is asked, cached and reported on its own. Two
providers issuing the same record are still both refused.
