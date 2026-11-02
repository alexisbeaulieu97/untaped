What a pack's hook leaves running in its worker's process group is stopped
when the worker ends (SIGTERM, then SIGKILL two seconds later); a helper meant
to outlive the run starts its own session.
