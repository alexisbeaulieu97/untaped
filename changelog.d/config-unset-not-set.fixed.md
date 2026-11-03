Removing a setting that isn't set, through `config unset`, setup or a plugin's
settings write, now changes nothing and reports it unchanged; a retired
spelling left behind is for `config migrate`. A setting set to null counts as
set, as runs read it, so removing it is reported.
