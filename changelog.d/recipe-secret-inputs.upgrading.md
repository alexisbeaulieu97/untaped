In each recipe's `inputs:`, rename `sensitive: true` to `secret: true`; a
recipe that still says `sensitive` fails to load, naming the key. Scripts
that read `recipe get` rows read `secret` instead of `sensitive`.
