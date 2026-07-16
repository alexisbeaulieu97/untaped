
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
