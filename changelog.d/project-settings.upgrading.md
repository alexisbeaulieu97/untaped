A cloned repository's committed `.untaped/config.yml` is read once you run
`untaped project init` there: read what it sets (its `profile:` and values)
before you do. A script that reads `untaped profile current -f json` finds
the project's file under `folder` (or `null`) and the names it skipped
under `skipped`.
