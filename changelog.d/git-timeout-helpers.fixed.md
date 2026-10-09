A Git command that times out now also stops the helper processes it started,
after giving Git a moment to remove its lock files, so the next fetch no
longer fails on a `shallow.lock` the timed-out one left behind.
