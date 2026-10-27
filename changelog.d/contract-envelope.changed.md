**Breaking (plugins):** a contract's records take their kind from their
class name at composition, as every 12.0 record does: the owner's model and
a provider's own record declare no `kind=`, and the `foreign-kind` reason
is gone from contracts. `Stdin(via=Contract.bridge)` reads the rows of any
provider's own record through that provider's bridge, building the provider
only when one of its rows arrives; an unready provider whose row arrived
exits 4. `convert`, `PipeEnvelope`, `is_envelope_line` and
`parse_envelope_line` are deprecated behaviours removed in 12.0.
