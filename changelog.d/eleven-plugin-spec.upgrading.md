Move the plugin to a 12.0 plugin class in a leaf `plugin.py` and point its
entry point at it (`acme = "untaped_acme.plugin:Acme"`), as the quarantine
line says. A test that gave `compose_with` a fake through `provides=`
composes a plugin class whose `fills` holds the fake provider.
