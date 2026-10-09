ansible: `source set` and `source patch` report an invalid source as one
line naming the problem instead of pydantic's full error dump, and
validation messages across untaped drop pydantic's `Value error, ` prefix.
([#483](https://github.com/alexisbeaulieu97/untaped/pull/483))
