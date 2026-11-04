A plugin may fill one contract with several providers, one per record of its
own, each asked, cached (its answer-cache key, as an `untaped.stale` line
shows it, is now `<plugin>:<kind>:<hash>`) and checked on its own, and
`Answers(pending=)` takes stale lines by answer position, while two providers
issuing one record are still both refused.
