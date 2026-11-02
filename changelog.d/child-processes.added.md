`untaped.sdk.process` (experimental) runs a child process in its own process
group, to the end with `run_process` or long-lived with `start_process`, and
stops the whole group (SIGTERM, then SIGKILL two seconds later) on a timeout,
an interrupt or a request to stop.
