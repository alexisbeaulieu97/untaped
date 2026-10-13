When a contract provider's live call fails and its cached answer is used,
stderr says so once with an `untaped.stale` warning naming the cause and the
answer's age; a cached answer with no match fails with the provider's error
alone. ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600),
[#629](https://github.com/alexisbeaulieu97/untaped/pull/629))
