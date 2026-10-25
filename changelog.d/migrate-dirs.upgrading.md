Run `untaped setup migrate-dirs` with 11.1 before upgrading. A plugin drops
its `migrations=` and a store's `bare=`; `dir_bytes` and `setting_guard` stay
in `untaped.sdk`.
