Delete a provider's committed `fills.json`; nothing reads it. A script
reading `owner-schema-drift` or the `fills`/`schema` rows from `doctor -f
json`, `plugin check` or `untaped lint` drops them.
