`untaped doctor` lists a deprecated name a plugin's code was seen using
while a command ran, such as a method on an object it got back, which no
import shows: its `plugin-uses` row names the command and the release that
removes the name. untaped records each one in its own state, once per plugin
version, name and command, without ever failing or slowing the command.
