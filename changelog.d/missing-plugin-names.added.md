A plugin command that reaches a name untaped or another plugin no longer has
(a method on an object it got back, a field a model dropped, a module looked
up while it runs) stops with an error naming the plugin, the name and the
fix, and exits 4, instead of a traceback; `--verbose` shows the traceback.
A name looked up while a plugin loads disables that plugin (`uses-missing`)
as a missing import does.
