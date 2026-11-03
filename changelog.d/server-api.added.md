Plugins can keep their commands independent of the server API version with
the experimental `ServerApi` block (`untaped.sdk.network`): commands call one
port, core detects the server's version, caches it per server for a day,
picks the adapter serving it, refuses a version none serves with exit 4, and
after a mismatch switches adapter and reruns the call when nothing took
effect. Each block adds a `<plugin>.server-api` doctor row (`--online`
detects again), a "Servers" section on the plugin's `untaped docs` page and
a `<plugin>.caches.server_api.max_age_seconds` setting.
