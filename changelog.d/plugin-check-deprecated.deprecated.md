`untaped plugin check` and `untaped.testing.check_conventions` are deprecated
in favour of `untaped lint` and `untaped_lint.testing.assert_clean`, and are
removed in 12.0. Both still print or fail on the same rows, but run the rules
that read a plugin's files only when `untaped[lint]` is installed, and warn
when it is not.
