`rejected_token_error("acme", message)` becomes
`rejected_token_error(Service(AcmeSettings), message)`, called inside the
run, so the error and its `auth login` hint name the plugin as it runs,
renamed or not.
