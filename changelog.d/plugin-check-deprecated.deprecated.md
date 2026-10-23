`untaped plugin check` and `untaped.testing.check_conventions` are deprecated
in favour of `untaped lint` (experimental) and
`untaped_lint.testing.assert_clean`, and are removed in 12.0. Both still print
or fail on the same rows, but run the rules that read a plugin's files only
when `untaped[lint]` is installed, and warn when it is not. A plugin's
convention test calls `assert_clean(NAME)` with `untaped[lint]>=11.1` in its
`dev` dependencies, and its CI runs `untaped lint` in the plugin's directory,
which exits 3 on an error finding where `untaped plugin check NAME` exited 1.
