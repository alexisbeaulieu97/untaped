A plugin that derives several commands from one base passes each its own
`qualname=`, the name its module binds it to (`PATCH = derive(Patch,
"Patch", qualname="PATCH", ...)`), or composition refuses the plugin with
`duplicate-target`.
