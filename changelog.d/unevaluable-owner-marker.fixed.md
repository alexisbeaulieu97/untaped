A plugin requirement whose environment marker can't be compared, such as
`python_version ~= "3"`, quarantines that plugin as `bad-metadata` instead of
crashing composition or `untaped lint` with a traceback.
