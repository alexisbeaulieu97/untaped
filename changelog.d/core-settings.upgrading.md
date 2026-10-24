In each profile of `config.yml`, move `http:`, `ui:` and `skills:` under an
`untaped:` key (`profiles.default.untaped.http.proxy`), and rename
`UNTAPED_HTTP__X` to `UNTAPED_UNTAPED__HTTP__X` (likewise `UI` and `SKILLS`).
A plugin reads them with `ctx.http` and `ctx.ui` as before.
