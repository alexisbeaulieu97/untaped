After a failed refresh of a provider's repo list, workspace serves the cached
list for 5 minutes without asking again (`ctrl-r` in the picker asks at once),
and while a cached list exists an automatic refresh gives up after 10 s
instead of retrying.
