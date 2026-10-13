The first 11.0 run takes each saved source's freshness from its last
`source refresh`, so nothing fresh is refreshed again. After that, scripts
that ran `impact` and friends on purpose against old data pass `--cached`.
