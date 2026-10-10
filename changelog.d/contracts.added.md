`untaped.contracts` lets a plugin declare an interface other plugins fill
(`Contract`, `@bridge`, `@cached`, `@listing`) and ask every provider at
once (`gather`, `select_one`, `convert`); doctor has a row per provider and
warns about the ones it set aside.
