+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6b3da73acb58a68dffdc85adc"
kind = "decision"
title = "Profiles and themes are absorbed into the SDK"
created_at = "2026-07-10T00:30:02.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "git:80bb8411cd0017f3e0cde818656aaf6fd0233368:docs/decisions.md#sha256:597d74559b5447942468b7fe321ab40dccbed32e4055d9fca71830702c55831e"
+++

Profile resolution and the profile management group (`create`/`list`/`use`/
`delete`), plus the built-in theme presets, are now part of the SDK and mounted
per tool by `run_tool`. The standalone `untaped-profile` and `untaped-themes`
packages are **retired** — a standalone tool would reintroduce "install one
more thing to manage shared state".
