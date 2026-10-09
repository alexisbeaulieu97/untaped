`auth set`, `auth migrate` and `setup` test the chosen store with a throwaway value
first (`pass`: gpg encrypt and decrypt; `secret-tool`: store and read back) and
stop with the cause instead of failing token by token; `pass` is skipped when
gpg holds no key for it, gpg's repeated errors are quoted once with the usual
fixes (also when a `pass` token command fails at use), and `doctor` fails a
`pass` token command that gpg cannot serve here.
([#510](https://github.com/alexisbeaulieu97/untaped/issues/510))
