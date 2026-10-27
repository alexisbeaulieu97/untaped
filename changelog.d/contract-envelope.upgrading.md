Drop `kind=` from a contract's owner model and a provider's own record (the
kind becomes the class name, so rename a class whose kind should stay). A
12.0 command reading piped rows declares `stdin: Stdin[Owner] =
Stdin(via=Contract.bridge)` and calls `self.stdin.values(given)` in place of
`convert(Contract.bridge, envelope)` over `PipeEnvelope`s. A fake handed to
`compose_with(provides=)` keeps its own record's `kind=`;
`install(Owner, providers=...)` names it by the providers' key instead.
