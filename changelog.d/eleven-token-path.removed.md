**Breaking (users):** `untaped auth set`, `auth unset` and `auth migrate`
and their `untaped.auth_outcome` rows: `auth login` stores a token and `auth
forget` deletes it. No plugin reads a token from `config.yml` any more, so
doctor's plain-text token warning and `setup`'s tab that moved one to the
store are gone, and `auth status` prints one row per credential.
