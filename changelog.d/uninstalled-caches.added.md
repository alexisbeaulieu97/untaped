`untaped doctor` has a `caches` row. It notes a cache's keys that hold only
another version's value, and the `cached/` folder of a plugin not installed
here. `untaped setup migrate-dirs` now offers to delete that folder; nothing
deletes it on its own, since another install sharing the home may still use it.
