`config list` prints the experimental settings under an `Experimental`
heading and the deprecated ones that are set (or all, with `untaped
--deprecated`) under `Deprecated`, with a `note` column only there;
`--format json` stays one list. `shell.aliases` is marked deprecated, and
`awx.test_timeout_seconds` and `awx.test_parallel` experimental.
([#519](https://github.com/alexisbeaulieu97/untaped/pull/519))
