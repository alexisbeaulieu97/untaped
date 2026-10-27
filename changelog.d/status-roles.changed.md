**Breaking (plugin authors):** a table no longer colours a cell because its
column is named `status`, `action`, `result`, `state` or `job_status`: it
colours the words its field's `Status` names, and core's outcome and check
records name theirs. A record still declaring `kind=` keeps the old word
list until its plugin moves to a plugin class.
