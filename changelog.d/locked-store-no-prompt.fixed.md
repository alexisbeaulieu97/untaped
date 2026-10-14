A command reading a token from a locked `pass` store with no terminal on
stdin or stderr, as when an agent runs it, no longer opens gpg's passphrase
prompt on the screen under the agent and waits for it; it fails at once and
names the command that unlocks the store from your own terminal. A pinentry
mode you set yourself is kept.
