`Output(columns=("name", "state"))` becomes
`Output(columns=(key(Repo).name, key(Repo).state))`, and
`self.output.columns.add("url")` becomes `.add(key(Repo).url)`.
`self.output.columns[:] = (...)` becomes `.show(...)`,
`.insert(0, "pack")` becomes `.add(key(Row).pack, first=True)`, and a
comprehension that removes one becomes `.drop(key(Row).kind)`. A class whose
own field is named `key` shadows the function inside its body: declare the
columns as a module constant above it.
