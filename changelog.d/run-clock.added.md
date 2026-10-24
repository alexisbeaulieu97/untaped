Cache ages, progress cadence and HTTP back-off all read the run's clock,
so one test clock moves all three: `untaped.sdk.testing.manual_clock()`
installs a `ManualClock` that moves on `advance(hours=1)` and on a
block's sleep, which never waits.
