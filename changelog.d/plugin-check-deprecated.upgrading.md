A plugin's convention test moves from `check_conventions(NAME)` to
`untaped_lint.testing.assert_clean(NAME)`, with `untaped[lint]>=11.1` in its
`dev` dependencies; a CI step running `untaped plugin check NAME` runs
`untaped lint` in the plugin's directory instead, which exits 3 on an error
finding where `plugin check` exited 1.
