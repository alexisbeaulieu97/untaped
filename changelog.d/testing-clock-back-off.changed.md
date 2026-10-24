HTTP back-off waits on the run's clock alone: a test's `clock` fixture makes a
retry instant, so patching `untaped.sdk.network.http._sleep` is no longer
needed (the function is gone). A cache refreshed at the same instant a later
run starts is refreshed again under `--refresh`, so a test's stopped clock
never serves a stale copy. `cache_clock` moves its wall clock on a block's
wait instead of waiting.
