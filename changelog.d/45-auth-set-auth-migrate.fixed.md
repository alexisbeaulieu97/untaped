A `pass` token command that fails quotes gpg's first error once, with the
usual fixes, instead of letting gpg repeat it on the terminal, and `doctor`
fails a `pass` token command that gpg cannot serve here (no gpg, or no key
for the password store).
([#511](https://github.com/alexisbeaulieu97/untaped/pull/511),
[#510](https://github.com/alexisbeaulieu97/untaped/issues/510))
