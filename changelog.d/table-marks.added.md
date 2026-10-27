A record field can say how a table shows it, where json, yaml, raw and pipe
keep the value: `Annotated[int, Bytes]` reads as a size (`1.5 KiB`),
`Annotated[datetime, Age]` as how long ago it was (`3 h ago`, by the run's
clock), and `Annotated[dict[str, int], Spread]` as one column per key, which
`--columns KEY` picks. A command names its `Spread` keys once it is built (a
model validator calling `self.output.columns.add(...)`, or `.offer(...)` for
a key hidden by default), so `--columns` is checked before `run`, and a named
key no row has is an empty column; `--columns` picks only the keys it named.
`untaped.sdk.ago_text(at, now)` gives the same words for a time elsewhere.
