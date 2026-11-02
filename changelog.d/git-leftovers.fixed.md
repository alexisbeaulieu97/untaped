A process a `git` command leaves behind that still holds git's output open is
stopped two seconds after git exits, instead of keeping untaped waiting until
the command's timeout; one that holds nothing, such as a credential cache
daemon, keeps running.
