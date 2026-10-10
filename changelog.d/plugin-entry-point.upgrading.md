A third-party plugin changes its entry point from `untaped_<name>:provider`
to `untaped_<name>:SPEC` and may delete its `provider()` function. A plugin
that fills another plugin's contract declares an `untaped` range and the
owner under an extra named like it, with a range
(`shelf = ["untaped-shelf>=1,<2"]`): `check_conventions` fails it otherwise,
and an installed owner outside that range quarantines the offer
(`owner-out-of-range`). Tests that called `provider_candidate` call
`plugin_candidate`.
