`untaped.sdk.now()` is the run's wall time in UTC, the one clock a plugin
reads: the testing kit's `clock.advance(hours=3)` moves it with core's own
stamps, cache ages and back-offs. Outside a run it raises.
