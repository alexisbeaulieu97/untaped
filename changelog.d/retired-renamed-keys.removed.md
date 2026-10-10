**Breaking (core):** the old names of the nine settings renamed in 10.1
(`http.timeout`, `awx.test_timeout`, …) and their `UNTAPED_*` variables are
no longer read. `untaped doctor` names any left in `config.yml`, and
`untaped config migrate` still renames them.
