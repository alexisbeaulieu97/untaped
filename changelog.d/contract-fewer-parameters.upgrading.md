A provider method whose parameter names differ from its contract method's,
such as `def lookup(self, _title)` renamed to quiet a linter, or one that
takes a parameter positional-only, is now quarantined (`uses-missing`): give
it the contract's parameter names. Declare the contract's full signature, or
`**kwargs: Any`, and raise your floor on the owner when you read a parameter
it added.
