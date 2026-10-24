A 12.0 plugin class declares a folder of its own as `notes = Dir()`
(`plugins/<plugin>/notes/`), or at a path setting with
`Dir(at=key(Settings).field)`; `file=` makes it one file and `layout=`
numbers what it holds inside it, `open()` creates it, and `locked(name)`
holds one thing in it. Operation locks are taken in one order: a plugin's
own before that of a plugin it requires.
