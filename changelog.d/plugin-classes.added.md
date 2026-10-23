`untaped.sdk` has the 12.0 plugin interface, not yet mounted: `Plugin`,
`Command`, `Group`, `derive` and `StubSkill`; the fields core fills
(`Output`, `Stdin`, `Lines`, `Confirm`, `Refresh`, `Parallel`, `Limit`);
the value types `File`, `ExistingFile`, `TextOrStdin`, `PathOrStdin`,
`KeyValue` and `KeyJson`; the marks `Positional`, `Short`, `AllowHyphen`,
`Column`, `Key`, `Following` and `Written`; and `key(Model).field` for a
setting key. Core can compose plugin classes into one declaration, checking
them and naming the class or field a problem is about; nothing uses it yet.
