**Breaking (plugin authors)**: `derive(base, name, qualname=...)` binds a
derived command where its module holds it (`PATCH = derive(Patch, "Patch",
qualname="PATCH", ...)`), so each command derived from one base has its own
`module:qualname`. Composition now refuses a plugin that mounts two different
command classes at one target (`duplicate-target`): a hint spelling one of
them named whichever was mounted first. awx's per-kind commands each have
their own, so its hints and `untaped lint` fixes name the right kind.
