**Plugin authors:** a 12.0 provider requires each plugin whose contract one of
its `fills` subclasses in its plain `dependencies`, with a range
(`untaped-shelf>=1,<2`), not under an extra named after it. `untaped lint`
reports "fills a contract of shelf, which it does not require", and `plugin
check` fails a fill whose owner isn't installed.
