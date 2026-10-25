A 12.0 plugin class declares a folder of its own as `notes = Dir()`
(`plugins/<plugin>/notes/`), or at a path setting with
`Dir(at=key(Settings).field)`; `file=` makes it one file and `layout=`
numbers what it holds inside it, `open()` creates it, and `locked(name)`
holds one thing in it. Operation locks are taken in one order: a plugin's
own before that of a plugin it requires. A registered install's reference
names a numbered place's layout, `prune()` removes the layouts nothing holds
after the plugin's complete refresh, and doctor's `stores` row lists them.
`Dir(items=..., release=..., moved=...)` lets holders such as projects hold
things in an unnumbered place, and `items()` is that item store.
