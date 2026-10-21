A cache now keeps each key's data and freshness together under
`plugins/<plugin>/cached/`, so most caches refill once after upgrading, and a
registered install removes 11.0's `caches/` folders by itself, with one
`untaped.pruned` line each; the ansible index keeps its data and only its
freshness is read again from it. Each file is readable by its owner only.
