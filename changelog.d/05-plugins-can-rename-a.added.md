Plugins can rename a setting by declaring `renamed_keys` on their settings
model: the old key and its `UNTAPED_*` variable keep working, with a
warning naming the new key, until the next major release.
([#475](https://github.com/alexisbeaulieu97/untaped/pull/475))
