**Breaking (sdk):** `ProgressHandle.update` takes `message=None, *, phase=,
done=, total=`; `fraction=`, `new_phase=`, `log()` and `report_error(write=)`
are gone, and `NO_PROGRESS` is the do-nothing handle.
