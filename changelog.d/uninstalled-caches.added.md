`untaped doctor` has a `caches` row. It notes a cache's files filled by
another version, and the `cached/` folder of a plugin not installed here.
`untaped setup migrate-dirs` now offers to delete that folder; nothing deletes
it on its own, since another install sharing the home may still use it.
