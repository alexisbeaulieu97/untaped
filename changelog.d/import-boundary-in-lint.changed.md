The `import-boundary` rule runs in `untaped lint` with the other file rules,
so `untaped plugin check` without `untaped[lint]` no longer reports it, and a
`# untaped: allow settings-naming` marker no longer waives a setting's name.
