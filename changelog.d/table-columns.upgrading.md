A record's `table_columns = ("name", "state")` becomes
`name: Annotated[str, Column]` and `state: Annotated[str, Column]` on those
fields. The table shows them in the order json shows the fields. An
`OutcomeRecord`'s `action` and a `CheckRecord`'s `status` are already marked,
so they join the record's own marks. To mark another inherited field, such as
a `TargetRecord`'s `target_path`, redeclare it. A command whose columns its
record's fields can't give (a subclass that wants none of its base's
columns, say) sets `Output(columns=(...))`.
