A provider method whose parameter names differ from its contract method's,
such as `def lookup(self, _title)` renamed to quiet a linter, one that takes
a parameter positional-only, or one taking `*args` without `**kwargs`, is
now quarantined (`uses-missing`): give it the contract's parameter names.
Declare the contract's full signature, or `**kwargs: Any`, and raise your
floor on the owner when you read a parameter it added. `assert_surface`
rewrites a plugin's snapshot in format 3, which untaped 11.x refuses to read
("surface format 3 is newer than this generator's"), so a plugin that still
runs its tests on 11.x skips `assert_surface` there.
