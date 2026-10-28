A plugin calls `connected_client(config, section=..., base_url=...,
bearer=...)` and declares its token as a `Credential` instead of
`TokenSources` and a `token` or `token_command` setting.
