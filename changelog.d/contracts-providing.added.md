`untaped.contracts.providing` composes provider instances as a plugin of
their own, for an owner that loads providers from somewhere other than an
installed package; composing the root from inside a contract method now
fails that provider instead of swapping the composition under its neighbours.
