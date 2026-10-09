The old names of the nine renamed settings (`http.timeout`,
`awx.test_timeout`, …) and their `UNTAPED_*` variables still work, with a
warning, until 11.0; `untaped config migrate` renames them in `config.yml`.
([#489](https://github.com/alexisbeaulieu97/untaped/pull/489))
