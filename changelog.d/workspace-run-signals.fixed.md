A SIGTERM or SIGHUP sent to untaped during `workspace run` (`kill`,
`timeout`, a closed terminal, a cancelled CI job) stops the commands it is
running; they used to run on after untaped ended.
