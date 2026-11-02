**Breaking (plugins):** a provider may take fewer parameters than its
contract method: core binds each call to the contract's signature, applies
its defaults and passes the provider only the parameters it declares, by
name, so an owner can add an optional parameter without breaking providers
released before it. A provider taking a parameter the contract doesn't
declare, or one positional-only, is quarantined alone (`uses-missing`). The
surface file is format 3: each optional parameter of a contract method has a
line of its own, and a contract's methods are listed only as `contract`
lines; `assert_surface` rewrites an older snapshot by itself.
