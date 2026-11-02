A plugin command that reaches a name untaped or another plugin no longer has
(a method on an object it got back, a module looked up while it runs) stops
with an error naming the plugin, the name, the owner's installed version,
what the plugin requires of it and both fixes, and exits 4, instead of a
traceback; `--verbose` shows the traceback. A contract provider that reaches
one answers with the same error.
