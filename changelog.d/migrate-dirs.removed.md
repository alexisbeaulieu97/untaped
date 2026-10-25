**Breaking (users):** `untaped setup migrate-dirs` and doctor's
`migrate-dirs` row are gone: 12.0 refuses an 11.x home, so it has nothing to
move. Ansible no longer renames 11.0's `index.sqlite3` to its layout's name;
it builds its index anew.
