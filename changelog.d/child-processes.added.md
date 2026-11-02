`untaped.sdk.process` runs a child process in its own process group, to the
end with `run_process` or long-lived with `start_process`, and stops the whole
group on a timeout, an interrupt or a request to stop: one soft signal (the
interrupt's own, or SIGTERM), then SIGKILL two seconds later.
