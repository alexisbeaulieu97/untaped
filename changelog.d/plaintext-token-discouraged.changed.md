A token stored in plain text in `config.yml` (`<section>.token`) is now
called discouraged instead of deprecated, in the warnings and the docs: it
keeps working and is not scheduled for removal. `UNTAPED_<SECTION>__TOKEN`
or a token command (`untaped auth migrate`) keeps it out of the file.
