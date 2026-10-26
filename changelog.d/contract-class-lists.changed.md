**Breaking (plugins):** a provider subclasses `Provider[S]` (was
`Configured[S]`) beside the contract it fills, or plain `Provider` when its
plugin reads no settings, and reaches `self.ctx` (`diag`, `project`,
`plugin`, `plugins`, no terminal). A plugin class lists the contracts it owns
in `offers` and its providers in `fills`; core reads each provider's owner
from the contract and builds it at the first ask.
