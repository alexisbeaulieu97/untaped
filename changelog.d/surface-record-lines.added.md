`tests/repo/surface.txt` lists record kinds and their fields (`record`
lines): core's stderr kinds now, and a 12.0 plugin class's kinds from its
declaration. A kind a command reads on `--stdin` or from a file also lists
its required fields and defaults, so a new required field or a changed
default fails the surface check.
