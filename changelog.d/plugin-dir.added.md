A 12.0 plugin class declares a folder of its own as `notes = Dir()`
(`plugins/<plugin>/notes/`), or at a path setting with
`Dir(at=key(Settings).field)`; `layout=` and `file=` number it as a store,
`open()` and `prune()` keep it, and `locked(name)` holds one thing in it.
Operation locks are taken in one order: a plugin's own before its
provider's.
