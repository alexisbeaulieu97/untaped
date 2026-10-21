The SDK's `project_root()` is now `checkout_root()`, which returns `None`
outside a git checkout instead of the start folder, so the caller picks its
fallback. `project_root()` keeps its answer, warns, and is removed in 12.0:
use `checkout_root(start) or start`.
