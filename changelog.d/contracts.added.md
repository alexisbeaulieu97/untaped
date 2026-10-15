`untaped.contracts` lets a plugin declare an interface other plugins fill
(`Contract`, `@bridge`, `@cached`, `@listing`) and ask every provider at
once (`gather`, `select_one`, `convert`); doctor has a row per provider and
warns about the ones it set aside. A provider that raised is `Failed` with
`reason="raised"`, and one that composes the root inside its call fails.
When a provider's live call fails and its cached answer is used, stderr says
so once with an `untaped.stale` warning naming the cause and the answer's
age; a cached answer with no match fails with the provider's error alone.
([#580](https://github.com/alexisbeaulieu97/untaped/pull/580),
[#600](https://github.com/alexisbeaulieu97/untaped/pull/600),
[#629](https://github.com/alexisbeaulieu97/untaped/pull/629))
