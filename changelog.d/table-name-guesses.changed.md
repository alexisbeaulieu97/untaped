**Breaking (plugin authors):** a table no longer formats a cell by its
column's name: a field named `*_s` or `elapsed` is a run time only when marked
`Duration`, and one named `sha`, `commit`, `revision` or `scm_revision` is
shortened only when marked `ObjectName`. untaped's own records carry the
marks, so their tables look as before. awx's job, project and unified template
records now declare `elapsed` and `scm_revision`, so those keys come before
AWX's other fields in json, yaml and pipe.
