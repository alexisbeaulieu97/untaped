`untaped config migrate` gives renamed and retired keys their new names in
every profile of `config.yml`, and `untaped doctor` lists such keys with it
as an automatic fix; `config set` and `config unset` also remove a key's old
spelling, and `config list` notes a value still read from one.
([#482](https://github.com/alexisbeaulieu97/untaped/pull/482),
[#485](https://github.com/alexisbeaulieu97/untaped/pull/485))
