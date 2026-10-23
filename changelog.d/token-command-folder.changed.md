Token and password-store commands run in the config file's folder instead of
the current one, and a `.` on `PATH` finds them, and `git`, there too. A
token command whose program is a relative path (`./get-token`) is refused:
give a bare name found on `PATH` or an absolute path, and give a file the
command reads as an absolute path too.
