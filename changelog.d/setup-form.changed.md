A service's form in `untaped setup` holds every setting its plugin marks as
needed, each in the field its type takes, and a source for each of its
secrets, not only a token. Saving writes only the settings you changed, in
one write, so a value the profile inherits stays inherited, and it writes
nothing when one of them changed in the config file since the form opened.
