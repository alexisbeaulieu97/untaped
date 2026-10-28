A script that passes a credential's key field to `auth login` or `auth
forget` as `--vault-id prod` passes `--key vault-id=prod`.
A script that runs `auth forget` without a terminal still
passes `--yes`; the error without it now names the question it would ask.
