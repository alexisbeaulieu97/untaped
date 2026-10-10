A plugin whose settings or state declared a field named `extensions` or
`caches` renames it and lists the old name in `renamed_keys`. A plugin that
owns a contract can't list `extensions` there, since untaped injects that key
into its section. A plugin named `caches` takes another name.
