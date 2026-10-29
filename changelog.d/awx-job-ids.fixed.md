`awx jobs` refuses a job id that isn't a positive integer before asking AWX,
naming the id once (`error: abc: not a job id (a positive integer)`); 0 and
negative ids were sent to AWX. `awx jobs wait --timeout` refuses a negative
value, as `awx launch --timeout` does, and its waits stop with the run.
