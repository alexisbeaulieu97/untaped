**Breaking (plugin authors)**: `untaped lint`'s `example-command` rule also
checks each command named in inline code in a command's docstring and its
options' help, so a mention of a command that no longer exists fails the
check; such a mention may leave out required arguments.
