**Breaking (core):** the old names of the nine settings renamed in 10.1
(`http.timeout`, `awx.test_timeout`, …) and their `UNTAPED_*` variables are
no longer read. `untaped doctor` names any left in `config.yml`, and any
`UNTAPED_*` variable of a retired or deleted setting still set, with what
replaces it; `untaped config migrate` still renames the keys.
([#589](https://github.com/alexisbeaulieu97/untaped/pull/589),
[#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
