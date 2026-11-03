Removing a setting that isn't set, through `config unset`, setup or a plugin's
settings write, now removes only its retired spellings, so `config migrate`
can't bring the value back, and reports them; with none, it reports the
setting unchanged. A setting set to null counts as set, as runs read it.
