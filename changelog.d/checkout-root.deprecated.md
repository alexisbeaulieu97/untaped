The SDK's `project_root()` is now `checkout_root()`, which returns `None`
outside a git checkout instead of the start folder, so the caller picks its
fallback. `project_root()` keeps its answer, warns, and is removed in 12.0:
use `checkout_root(start) or start`. `untaped skills install --scope local`
and `untaped awx test` now find the checkout the same way, without running
git, so they work where git isn't installed.
