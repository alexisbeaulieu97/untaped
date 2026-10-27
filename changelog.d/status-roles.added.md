Plugin authors can say how a table colours a field's words with `Status`:
`Annotated[RepoAction, Status(success=("cloned",))]` names the theme role
(`success`, `warning` or `error`) each word is drawn in, so a user theme
recolours them. A field adds to the roles of the field it redeclares, so
`OutcomeRecord.action` keeps core's outcome words and `CheckRecord.status`
its check words.
