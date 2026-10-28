**Breaking (plugins):** `untaped.sdk.TokenCommand`, `TokenSources` and
`connection_check`; `connected_client`'s `required=`, `base_url_field=` and
`bearer_token_field=` (pass `base_url=`, now required, and `bearer=`); the
`untaped.plaintext-token`, `untaped.unmarked-service` and
`untaped.connected-client-by-name` behaviours; and lint's
`lint.unmarked-service` rule. A service marks what it needs with `NeedsSetup`
and declares each secret as a `Credential`; a plugin that marks nothing has
no `setup` row and its online check always probes.
