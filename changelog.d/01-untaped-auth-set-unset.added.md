`untaped auth set|unset|status|migrate` store API tokens in the machine's
password store instead of `config.yml`. `auth set`, `auth migrate` and
`untaped setup` first test the chosen store with a throwaway value (`pass`:
gpg encrypt and decrypt; `secret-tool`: store and read back) and stop with
the cause. A capability named `auth` is now quarantined like the other
management command names.
([#442](https://github.com/alexisbeaulieu97/untaped/pull/442),
[#510](https://github.com/alexisbeaulieu97/untaped/issues/510))
