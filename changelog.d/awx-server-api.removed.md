**Breaking (users):** the setting `awx.api_prefix` is removed: untaped finds
the controller API's prefix from the server's version. A context path stays
in `awx.server.url`.
