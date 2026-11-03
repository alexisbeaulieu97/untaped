In each recipe's `inputs:`, rename `sensitive: true` to `secret: true`; a
recipe that still says `sensitive` fails to load, naming the key. Scripts that
read `recipe get` rows read `secret` instead of `sensitive`. A script that
checks `recipe apply`'s exit code sees 2 (usage) instead of 1 for an unknown
`--var` or `--input-from` name, an `--input-from` naming a global input, or an
`--input-from` expression that doesn't compile.
