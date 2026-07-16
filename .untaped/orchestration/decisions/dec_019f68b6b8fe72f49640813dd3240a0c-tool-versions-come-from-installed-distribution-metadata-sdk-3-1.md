+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6b8fe72f49640813dd3240a0c"
kind = "decision"
title = "Tool versions come from installed distribution metadata (SDK 3.1)"
created_at = "2026-07-10T00:30:02.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "git:80bb8411cd0017f3e0cde818656aaf6fd0233368:docs/decisions.md#sha256:597d74559b5447942468b7fe321ab40dccbed32e4055d9fca71830702c55831e"
+++

Each tool is independently packaged and installed, so its CLI reports the
version of its own distribution rather than the version of the shared SDK.
`ToolSpec.distribution` identifies that package and defaults to `command`; an
explicit override covers executables whose name differs from their package.
`run_tool` gives Cyclopts a callable so metadata resolution is lazy and happens
only when `--version` is requested, not during ordinary startup or app wiring.

Cyclopts' existing version-only output remains the contract: stdout is exactly
the installed version plus a trailing newline. The shared release smoke invokes
the real installed console script with `--version` and requires that exact
output before checking help, so packaging metadata, entry-point wiring, and the
public CLI behavior are verified together.
