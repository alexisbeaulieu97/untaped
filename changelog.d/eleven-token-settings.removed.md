**Breaking (plugins):** a plugin that declares a credential no longer gets
11.0's `<name>` and `<name>_command` settings in its section, so
`Credential.get` reads none; `HttpSettings` loses `verify_ssl` and
`verify_hostname`.
