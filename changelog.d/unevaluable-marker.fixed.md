A plugin whose requirement on untaped has an environment marker that cannot
be compared, such as `python_version ~= "3"`, is quarantined as bad metadata
instead of stopping every command with a traceback.
