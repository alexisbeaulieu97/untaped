# Caches

A cache is data a plugin keeps from an upstream it can ask again (an index
built from GitHub, a listing), with a max age after which it is checked
before use. Declare one `Cache` per thing your plugin keeps, beside your
settings, and serve every read through it rather than writing your own
freshness check, `--refresh` flag or stale warning:

```python
from datetime import timedelta
from typing import Annotated

from untaped.sdk import Cache, echo, refresh_option, report_errors

MOTD = Cache("acme", "motd", label="message of the day", max_age=timedelta(hours=1))


@app.command(name="motd")
def motd(*, refresh: Annotated[bool | None, refresh_option(MOTD)] = None) -> None:
    """Print the message of the day."""
    with report_errors():
        served = MOTD.serve({"motd": "the message of the day"}, refresh=refresh,
                            fill=fetch_motd, read=read_motd)
    echo(served.value)
```

`serve(keys, refresh=, fill=, read=)` takes the keys the command reads,
each with a label for a human (`{"prod": "source prod"}`). Per key, it
refreshes when the directive asks: `None` (the default) when the key is older
than its max age, `--refresh` now, `--cached` never. `fill(key, handle)`
fetches one key into your own store and returns a `Filled`, reporting
progress through `handle`; `read()` then computes the command's answer once,
over every key. `serve()` opens the refresh's progress task, holds the key's
lock while it fills (another process filling the same key is waited for,
then served), and keeps what it knows about each key in a file under
`plugin_dir(SPEC)/caches/`, per profile: keep your own store apart the same
way (by key, and by profile when a setting can change what a key holds).
A fill never serves its own cache; serving another one, it passes its
`handle` as `serve(progress=)`, so the inner refresh shows as its phase.

An automatic refresh of cached data fails fast: until one call succeeds,
every HTTP request and networked `git` command (`clone`, `fetch`,
`ls-remote`, `pull`) inside the fill is tried once, with a short timeout, so an offline user gets the cached data in
seconds; `--refresh` and a key's first fill keep the full timeouts and
retries. When a fill raises an `UntapedError`, `serve()` serves the cached
data with one `untaped.stale` warning and does not try again for five
minutes, or until the time the fill put in the error's `details["retry_at"]`
(ISO 8601; a rate limit's reset). With nothing cached it raises the error,
and the next run fills again. A fill that completed but left items out returns
`Filled(partial=Failure(...))`: every read warns until a whole refresh. Pass
`empty=` to say when an answer found nothing: a stale empty answer then fails
with the refresh's error instead of passing for an absence. `--cached` on a
key never filled raises `NeverRefreshed`.

Two rules keep every cache alike. Declare the directive as
`Annotated[bool | None, refresh_option(CACHE)] = None`, never a hand-made
`--refresh` or `--cached`: its help names the cache's setting and default
(`tests/repo/test_refresh_option.py` checks first-party code). And never
print a stale warning yourself: `serve()` says it once per key.

For a command that reports a cache's state, subclass `FreshnessRecord` and
fill it with `FreshnessRecord.fields(MY_CACHE.freshness(key), max_age=…,
now=…)`; its `refresh_error` never fails the row. `cached(key, read)` peeks
at what is cached with no refresh, lock or warning, for in-process readers
such as a screen; `seed()` and `forget()` record what an owner already holds
or forget a key whose data changed. In tests, `untaped.testing.cache_clock()`
moves the clock a cache reads. The example plugin (`examples/untaped-hello`,
`hello motd`) has one cache.
