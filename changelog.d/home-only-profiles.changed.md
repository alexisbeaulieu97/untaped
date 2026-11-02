**Breaking (users):** a setting only the home's base sets, such as
`git.store_dir`, is no longer read from a profile: a run whose picked profile
sets one exits 4, `config set --profile` refuses it, `profile create
--copy-from` leaves it out, and doctor's `untaped.home-only` row names one in
a profile nobody picks.
