Every command runs under its invocation's run, a command still mounted the
11.x way included, so a block such as `Dir` works under it; a 12.0 command's
own run nests inside.
